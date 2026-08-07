"""Leitor de OFX — 1.x (SGML, tags sem fechamento) e 2.x (XML).

Parse próprio por regex + máquina de estados sobre a sequência de tags, sem
ElementTree: o SGML do OFX 1.x não é XML válido (elementos-folha não fecham) e
o tokenizador único cobre os dois dialetos. Por não usar parser XML, o módulo
é imune a XXE por construção.

Extraído de cada ``STMTTRN``: TRNTYPE, DTPOSTED (AAAAMMDD com ou sem hora e
fuso ``[-3:BRT]``), TRNAMT (ponto decimal, convertido por string — nunca
float), FITID → ``id_banco``, CHECKNUM/REFNUM → ``documento``, NAME/MEMO →
``descricao``. ``CURDEF`` dá a moeda; ``LEDGERBAL``/``AVAILBAL`` viram
``SaldoInformado``; ``DTSTART``/``DTEND`` dão o período. ``CCSTMTRS`` marca
fatura de cartão (tipos compra/estorno); extrato de conta usa receita/despesa.
O sinal de TRNAMT manda na direção quando divergir do TRNTYPE.
"""

from __future__ import annotations

import codecs
import html
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

from ..detect import decodifica_texto
from ..dinheiro import casas_da_moeda, moeda_valida, parse_centavos
from ..modelos import (
    LinhaBruta,
    ResultadoParse,
    SaldoInformado,
    Transacao,
    fingerprint_transacao,
    normaliza_descricao,
)
from .base import ContextoParse

# ---------------------------------------------------------------------------
# Expressões do tokenizador e do cabeçalho
# ---------------------------------------------------------------------------

#: Uma tag (de abertura ou fechamento) e o texto até a próxima tag.
_RE_ELEMENTO = re.compile(r"<(/?)([A-Za-z0-9_.\-]+)[^>]*>([^<]*)")
_RE_XML_ENCODING = re.compile(r"encoding\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_RE_DATA_OFX = re.compile(r"\s*(\d{4})(\d{2})(\d{2})")
_RE_AMT = re.compile(r"^[0-9]*(\.[0-9]*)?$")

#: TRNTYPE → direção padrão (o sinal do TRNAMT prevalece quando divergir).
_TRNTYPE_CREDITO = frozenset({"CREDIT", "DEP", "DIRECTDEP"})
_TRNTYPE_DEBITO = frozenset(
    {"DEBIT", "PAYMENT", "ATM", "POS", "CHECK", "DIRECTDEBIT", "REPEATPMT", "CASH"}
)
#: TRNTYPE com tipo contábil próprio, independente do lado.
_TRNTYPE_TIPO = {"XFER": "transferencia", "FEE": "tarifa", "SRVCHG": "tarifa", "INT": "juros"}

#: Agregados que marcam contexto de cartão de crédito.
_TAGS_CARTAO = frozenset({"CCSTMTRS", "CCACCTFROM", "CCSTMTTRNRS"})


# ---------------------------------------------------------------------------
# Encoding do cabeçalho
# ---------------------------------------------------------------------------


def _normaliza_encoding(nome: str) -> Optional[str]:
    """Nome de codec válido para o Python ou ``None``."""
    try:
        return codecs.lookup(nome.strip()).name
    except (LookupError, ValueError):
        return None


def _resolve_encoding(conteudo: bytes, padrao: Optional[str]) -> str:
    """Respeita ENCODING/CHARSET do header OFX 1.x ou o prólogo XML do 2.x."""
    prefixo = conteudo[:2048].decode("latin-1", errors="replace")
    aparado = prefixo.lstrip("﻿ \t\r\n")
    if aparado.startswith("<?xml"):
        achado = _RE_XML_ENCODING.search(aparado[:200])
        if achado:
            nome = _normaliza_encoding(achado.group(1))
            if nome:
                return nome
        return padrao or "utf-8"
    cabecalho: Dict[str, str] = {}
    for linha in prefixo.splitlines():
        if "<" in linha:
            break  # começou o corpo SGML
        if ":" in linha:
            chave, _, valor = linha.partition(":")
            cabecalho[chave.strip().upper()] = valor.strip().upper()
    encoding = cabecalho.get("ENCODING", "")
    charset = cabecalho.get("CHARSET", "")
    if encoding in {"UTF-8", "UTF8", "UNICODE"}:
        return "utf-8"
    if charset in {"1252", "CP1252", "WINDOWS-1252"}:
        return "cp1252"
    if charset in {"ISO-8859-1", "LATIN-1", "LATIN1", "8859-1"}:
        return "latin-1"
    if encoding == "USASCII":
        return "cp1252"  # cp1252 é superconjunto do ASCII — decodifica sem perda
    return padrao or "cp1252"


def _decodifica(conteudo: bytes, contexto: ContextoParse) -> str:
    encoding = _resolve_encoding(conteudo, contexto.formato.encoding)
    try:
        return conteudo.decode(encoding, errors="replace")
    except LookupError:
        texto, _ = decodifica_texto(conteudo)
        return texto if texto is not None else conteudo.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Valores e datas OFX
# ---------------------------------------------------------------------------


def _amt_para_centavos(texto: Optional[str], moeda: str) -> Optional[int]:
    """TRNAMT/BALAMT: ponto decimal (spec OFX), convertido 100% por string.

    ``"1234.56"`` → 123456 e ``"-15.9"`` → -1590, exatos. Vírgula decimal
    (banco fora da spec) é aceita; milhar/formato regional cai no parser
    geral de dinheiro — nunca em ``float``.
    """
    if texto is None:
        return None
    bruto = texto.strip().replace(" ", "")
    if not bruto:
        return None
    negativo = False
    if bruto.startswith("(") and bruto.endswith(")"):  # convenção contábil
        negativo, bruto = True, bruto[1:-1]
    if bruto.startswith(("+", "-")):
        negativo = negativo or bruto.startswith("-")
        bruto = bruto[1:]
    corpo = bruto.replace(",", ".", 1) if ("," in bruto and "." not in bruto) else bruto
    if not _RE_AMT.match(corpo) or corpo in ("", "."):
        # Tem milhar ou formato regional misto: delega ao parser geral.
        return parse_centavos(texto, moeda)
    inteiro, _, fracao = corpo.partition(".")
    casas = casas_da_moeda(moeda)
    base = int(inteiro or "0") * (10**casas)
    if casas:
        util = fracao[:casas].ljust(casas, "0")
        base += int(util)
        extra = fracao[casas : casas + 1]
        if extra and int(extra) >= 5:  # arredondamento meio-para-cima
            base += 1
    return -base if negativo else base


def _data_ofx(texto: Optional[str]) -> Optional[date]:
    """AAAAMMDD, com ou sem hora e fuso (``20260105120000[-3:BRT]``)."""
    if not texto:
        return None
    achado = _RE_DATA_OFX.match(texto)
    if not achado:
        return None
    try:
        return date(int(achado.group(1)), int(achado.group(2)), int(achado.group(3)))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Normalização de cada STMTTRN
# ---------------------------------------------------------------------------


def _direcao_de(trntype: str, centavos: int) -> str:
    """O sinal do TRNAMT manda; TRNTYPE só desempata valor zero."""
    if centavos > 0:
        return "credito"
    if centavos < 0:
        return "debito"
    return "debito" if trntype in _TRNTYPE_DEBITO else "credito"


def _tipo_de(trntype: str, direcao: str, eh_cartao: bool) -> str:
    especial = _TRNTYPE_TIPO.get(trntype)
    if especial:
        return especial
    if eh_cartao:
        return "compra" if direcao == "debito" else "estorno"
    return "despesa" if direcao == "debito" else "receita"


def _descricao_de(bruto: Dict[str, str]) -> str:
    """NAME e MEMO concatenados quando ambos existem (e diferem)."""
    name = (bruto.get("NAME") or "").strip()
    memo = (bruto.get("MEMO") or "").strip()
    if name and memo and normaliza_descricao(name) != normaliza_descricao(memo):
        return f"{name} - {memo}"
    return name or memo


class AdaptadorOfx:
    """Adaptador registrado para o formato ``ofx`` (1.x SGML e 2.x XML)."""

    nome = "ofx"
    versao = "1.0"

    def detecta(self, contexto: ContextoParse) -> float:
        return 0.95 if contexto.formato.formato == "ofx" else 0.0

    def parse(self, contexto: ContextoParse) -> ResultadoParse:  # noqa: C901 - máquina de estados
        resultado = ResultadoParse(adaptador=self.nome)
        texto = _decodifica(contexto.conteudo, contexto)
        if "<" not in texto:
            resultado.avisos.append("OFX sem corpo de tags reconhecível — nada importado.")
            return resultado
        corpo = texto[texto.find("<") :]

        # --- fase 1: máquina de estados sobre a sequência de tags -----------
        pilha: List[str] = []
        trn_atual: Optional[Dict[str, str]] = None
        saldo_atual: Optional[Dict[str, str]] = None
        trn_eh_cartao = False
        viu_cartao = contexto.tipo_documento == "fatura_cartao"
        moedas: List[str] = []
        datas_inicio: List[date] = []
        datas_fim: List[date] = []
        registros: List[Tuple[Dict[str, str], bool]] = []
        saldos_brutos: List[Dict[str, str]] = []

        def _fecha_pendentes(tag: str) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, str]]]:
            """Finaliza registro/saldo abertos ao fechar (ou reabrir) agregados."""
            nonlocal trn_atual, saldo_atual
            if tag == "STMTTRN" and trn_atual is not None:
                registros.append((trn_atual, trn_eh_cartao))
                trn_atual = None
            elif tag in ("LEDGERBAL", "AVAILBAL") and saldo_atual is not None:
                saldos_brutos.append(saldo_atual)
                saldo_atual = None
            return trn_atual, saldo_atual

        for achado in _RE_ELEMENTO.finditer(corpo):
            fechamento = achado.group(1) == "/"
            tag = achado.group(2).upper()
            valor = html.unescape(achado.group(3)).strip()

            if fechamento:
                if tag in pilha:
                    while pilha:
                        topo = pilha.pop()
                        _fecha_pendentes(topo)
                        if topo == tag:
                            break
                else:  # fechamento órfão (SGML fora de ordem) — ainda finaliza
                    _fecha_pendentes(tag)
                continue

            if valor:
                # Elemento-folha com conteúdo (no SGML nunca há fechamento).
                if trn_atual is not None:
                    trn_atual.setdefault(tag, valor)
                elif saldo_atual is not None:
                    saldo_atual.setdefault(tag, valor)
                elif tag == "CURDEF":
                    moedas.append(valor.upper())
                elif tag == "DTSTART":
                    dt = _data_ofx(valor)
                    if dt is not None:
                        datas_inicio.append(dt)
                    else:
                        resultado.avisos.append(f"DTSTART inválido ignorado: {valor!r}")
                elif tag == "DTEND":
                    dt = _data_ofx(valor)
                    if dt is not None:
                        datas_fim.append(dt)
                    else:
                        resultado.avisos.append(f"DTEND inválido ignorado: {valor!r}")
                continue

            # Abertura de agregado.
            if tag == "STMTTRN":
                _fecha_pendentes(tag)  # SGML sem </STMTTRN> explícito (defensivo)
                trn_atual = {}
                trn_eh_cartao = viu_cartao or any(t in _TAGS_CARTAO for t in pilha)
            elif tag in ("LEDGERBAL", "AVAILBAL"):
                saldo_atual = {"__TAG__": tag}
            elif tag in _TAGS_CARTAO:
                viu_cartao = True
            pilha.append(tag)

        # Arquivo truncado: aproveita o que ficou aberto.
        _fecha_pendentes("STMTTRN")
        _fecha_pendentes(saldo_atual.get("__TAG__", "LEDGERBAL") if saldo_atual else "LEDGERBAL")

        # --- fase 2: normalização ------------------------------------------
        moedas_validas = [m for m in moedas if moeda_valida(m)]
        moeda = moedas_validas[0] if moedas_validas else (contexto.moeda or "BRL")
        if len(set(moedas_validas)) > 1:
            resultado.avisos.append(
                f"múltiplos CURDEF no arquivo ({', '.join(sorted(set(moedas_validas)))}); usando {moeda}"
            )
        resultado.moeda = moeda

        ocorrencias: Dict[Tuple[str, int, str, str, str], int] = {}
        for indice, (bruto, eh_cartao) in enumerate(registros, start=1):
            ref = f"STMTTRN {indice}"
            resultado.linhas_brutas.append(
                LinhaBruta(ordem=indice, origem_ref=ref, conteudo=dict(bruto))
            )
            data = _data_ofx(bruto.get("DTPOSTED"))
            if data is None:
                resultado.erros.append(
                    {
                        "origem_ref": ref,
                        "campo": "DTPOSTED",
                        "mensagem": f"data inválida ou ausente: {bruto.get('DTPOSTED', '')!r}",
                    }
                )
                continue
            centavos = _amt_para_centavos(bruto.get("TRNAMT"), moeda)
            if centavos is None:
                resultado.erros.append(
                    {
                        "origem_ref": ref,
                        "campo": "TRNAMT",
                        "mensagem": f"valor inválido ou ausente: {bruto.get('TRNAMT', '')!r}",
                    }
                )
                continue
            trntype = (bruto.get("TRNTYPE") or "").upper()
            direcao = _direcao_de(trntype, centavos)
            descricao = _descricao_de(bruto)
            documento = bruto.get("CHECKNUM") or bruto.get("REFNUM")
            valor_abs = abs(centavos)
            chave = (
                data.isoformat(),
                valor_abs,
                moeda,
                normaliza_descricao(descricao),
                normaliza_descricao(documento),
            )
            ocorrencias[chave] = ocorrencias.get(chave, 0) + 1
            resultado.transacoes.append(
                Transacao(
                    data_operacao=data,
                    valor_centavos=valor_abs,
                    moeda=moeda,
                    direcao=direcao,
                    tipo=_tipo_de(trntype, direcao, eh_cartao),
                    descricao_original=descricao,
                    descricao_normalizada=normaliza_descricao(descricao),
                    fingerprint=fingerprint_transacao(
                        contexto.rotulo_origem,
                        data,
                        valor_abs,
                        moeda,
                        descricao,
                        documento,
                        ocorrencias[chave],
                    ),
                    origem_ref=ref,
                    id_banco=bruto.get("FITID"),
                    documento=documento,
                )
            )

        for bruto in saldos_brutos:
            rotulo = "saldo final" if bruto.get("__TAG__") == "LEDGERBAL" else "saldo disponível"
            valor_saldo = _amt_para_centavos(bruto.get("BALAMT"), moeda)
            data_saldo = _data_ofx(bruto.get("DTASOF"))
            if data_saldo is None and datas_fim:
                data_saldo = max(datas_fim)
            if valor_saldo is None:
                resultado.avisos.append(
                    f"{rotulo}: BALAMT inválido ({bruto.get('BALAMT', '')!r}) — ignorado"
                )
                continue
            if data_saldo is None:
                resultado.avisos.append(f"{rotulo}: sem DTASOF nem DTEND — ignorado")
                continue
            resultado.saldos.append(
                SaldoInformado(
                    data=data_saldo, valor_centavos=valor_saldo, origem="extrato", rotulo=rotulo
                )
            )

        datas_trn = [t.data_operacao for t in resultado.transacoes]
        resultado.periodo_inicio = min(datas_inicio) if datas_inicio else (min(datas_trn) if datas_trn else None)
        resultado.periodo_fim = max(datas_fim) if datas_fim else (max(datas_trn) if datas_trn else None)

        if not registros:
            resultado.avisos.append("nenhum STMTTRN encontrado no OFX.")
        return resultado


ADAPTADORES = [AdaptadorOfx()]
