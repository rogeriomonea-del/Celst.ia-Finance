"""Leitor genérico de XML de lançamentos — heurístico e endurecido contra XXE.

Segurança PRIMEIRO: qualquer documento contendo ``<!DOCTYPE`` ou ``<!ENTITY``
é recusado com erro claro antes de qualquer parse — o ElementTree só vê XML
sem DTD, o que elimina XXE e bombas de entidade por construção.

Heurística de reconhecimento: procura conjuntos de elementos repetidos (mesmo
caminho e mesma tag) cujos filhos-folha/atributos contenham algo-data e
algo-valor (idealmente também descrição), mapeando sinônimos pt-BR/EN como o
leitor de CSV. Sem padrão reconhecível, devolve ``mapeamento_necessario`` com
a estrutura encontrada (caminhos + amostra) para o mapeador manual da UI.
``origem_ref`` = caminho do elemento + índice, ex.: ``extrato/lanc[3]``.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import deque
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...workbook import as_date
from ..detect import decodifica_texto
from ..dinheiro import parse_centavos
from ..modelos import (
    LinhaBruta,
    ResultadoParse,
    Transacao,
    fingerprint_transacao,
    normaliza_descricao,
)
from .base import ContextoParse

#: DTD em qualquer forma é recusada — proteção XXE antes de qualquer parse.
_RE_XXE = re.compile(r"<!\s*(doctype|entity)", re.IGNORECASE)
_RE_AAAAMMDD = re.compile(r"^\s*(\d{4})(\d{2})(\d{2})\s*$")

#: Sinônimos de nomes de campo (normalizados) → papel canônico.
_SINONIMOS: Dict[str, frozenset] = {
    "data": frozenset(
        {
            "data", "date", "dt", "dia", "datamovimento", "datamov", "datalancamento",
            "dataoperacao", "datacompra", "dtposted", "datalanc", "datapagamento",
        }
    ),
    "valor": frozenset(
        {"valor", "value", "amount", "amt", "vlr", "vl", "montante", "quantia", "valorlancamento"}
    ),
    "descricao": frozenset(
        {
            "descricao", "desc", "historico", "hist", "memo", "detalhe", "detalhes",
            "description", "lancamento", "nome", "name", "observacao", "obs", "texto", "titulo",
        }
    ),
    "documento": frozenset(
        {"documento", "doc", "numdoc", "numerodocumento", "numero", "referencia", "ref", "checknum", "nrdoc"}
    ),
    "saldo": frozenset({"saldo", "balance", "saldoapos", "saldofinal"}),
    "credito": frozenset({"credito", "credit", "entrada", "valorcredito", "cred"}),
    "debito": frozenset({"debito", "debit", "saida", "valordebito", "deb"}),
    "direcao": frozenset({"dc", "tipo", "natureza", "sinal", "tipolancamento", "debitocredito", "operacao"}),
    "contraparte": frozenset({"contraparte", "favorecido", "beneficiario", "pagador", "destinatario"}),
}

#: Papéis aceitos num mapeamento manual vindo da UI.
_PAPEIS_VALIDOS = frozenset(_SINONIMOS) | {"ignorar"}


def _localname(tag: object) -> str:
    """Nome local sem namespace; comentários/PIs (tag não-string) viram ``""``."""
    if not isinstance(tag, str):
        return ""
    return tag.rpartition("}")[2]


def _norm_nome(nome: str) -> str:
    """Minúsculas, sem acento, só alfanumérico — para casar sinônimos."""
    plano = unicodedata.normalize("NFKD", str(nome))
    plano = "".join(ch for ch in plano if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", plano.lower())


def _campos_do_registro(elem: ET.Element) -> Dict[str, str]:
    """Atributos + filhos-folha (e um nível de netos-folha) como dict plano."""
    campos: Dict[str, str] = {}
    for nome, valor in elem.attrib.items():
        chave = _localname(nome)
        if chave:
            campos.setdefault(chave, str(valor).strip())
    for filho in elem:
        nome = _localname(filho.tag)
        if not nome:
            continue
        if len(filho) == 0:
            campos.setdefault(nome, (filho.text or "").strip())
        else:  # um nível extra é comum: <valor><total>…</total></valor>
            for neto in filho:
                nome_neto = _localname(neto.tag)
                if nome_neto and len(neto) == 0:
                    campos.setdefault(f"{nome}.{nome_neto}", (neto.text or "").strip())
    if not campos and (elem.text or "").strip():
        campos["_texto"] = (elem.text or "").strip()
    return campos


def _mapeia_papeis(
    nomes: Iterable[str], manual: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """Papel canônico → nome do campo de origem (manual vence sinônimo)."""
    nomes = list(nomes)
    papeis: Dict[str, str] = {}
    if manual:
        for origem, papel in manual.items():
            if papel in _PAPEIS_VALIDOS and papel != "ignorar" and origem in nomes:
                papeis.setdefault(papel, origem)
    ignorados = {o for o, p in (manual or {}).items() if p == "ignorar"}
    for nome in nomes:
        if nome in ignorados:
            continue
        chaves = {_norm_nome(nome)} | {_norm_nome(parte) for parte in nome.split(".")}
        for papel, sinonimos in _SINONIMOS.items():
            if papel not in papeis and chaves & sinonimos:
                papeis[papel] = nome
                break
    return papeis


def _qualifica(papeis: Dict[str, str]) -> bool:
    """Um grupo serve quando tem algo-data e algo-valor (ou colunas C/D)."""
    return "data" in papeis and ("valor" in papeis or "credito" in papeis or "debito" in papeis)


def _para_data(texto: str) -> Optional[date]:
    """DD/MM/AAAA, ISO (com hora) e AAAAMMDD — sem exceção para fora."""
    if not texto:
        return None
    convertida = as_date(texto)
    if convertida is not None:
        return convertida
    achado = _RE_AAAAMMDD.match(texto)
    if achado:
        try:
            return date(int(achado.group(1)), int(achado.group(2)), int(achado.group(3)))
        except ValueError:
            return None
    return None


def _direcao_do_texto(texto: Optional[str]) -> Optional[str]:
    """"D"/"débito"/"saída" → debito · "C"/"crédito"/"entrada" → credito."""
    chave = _norm_nome(texto or "")
    if not chave:
        return None
    if chave in {"d", "db", "deb", "s", "saida"} or chave.startswith("debit"):
        return "debito"
    if chave in {"c", "cr", "cred", "e", "entrada"} or chave.startswith("credit"):
        return "credito"
    return None


class AdaptadorXmlGenerico:
    """Adaptador registrado para o formato ``xml`` (não-OFX)."""

    nome = "xml-generico"
    versao = "1.0"

    def detecta(self, contexto: ContextoParse) -> float:
        return 0.5 if contexto.formato.formato == "xml" else 0.0

    def parse(self, contexto: ContextoParse) -> ResultadoParse:  # noqa: C901 - heurística
        moeda = contexto.moeda or "BRL"
        resultado = ResultadoParse(adaptador=self.nome, moeda=moeda)

        texto, _ = decodifica_texto(contexto.conteudo)
        if texto is None:
            resultado.erros.append(
                {"origem_ref": "documento", "mensagem": "não foi possível decodificar o XML como texto."}
            )
            return resultado

        # Proteção XXE PRIMEIRO: DTD em qualquer forma = recusa, nunca parse.
        if _RE_XXE.search(texto):
            resultado.erros.append(
                {
                    "origem_ref": "documento",
                    "mensagem": (
                        "XML recusado por segurança: contém <!DOCTYPE ou <!ENTITY "
                        "(risco de XXE). Remova a DTD e reenvie o arquivo."
                    ),
                }
            )
            return resultado

        try:
            raiz = ET.fromstring(contexto.conteudo)
        except ET.ParseError:
            try:  # bytes sem declaração de encoding compatível: tenta o texto decodificado
                raiz = ET.fromstring(texto)
            except (ET.ParseError, ValueError) as exc:
                resultado.erros.append(
                    {"origem_ref": "documento", "mensagem": f"XML malformado: {exc}"}
                )
                return resultado

        # ------------------------------------------------------------------
        # Agrupa elementos por (caminho do pai, tag) — candidatos a registro.
        # ------------------------------------------------------------------
        grupos: Dict[Tuple[str, str], List[ET.Element]] = {}
        fila: deque = deque([(raiz, _localname(raiz.tag) or "raiz")])
        while fila:
            elem, caminho = fila.popleft()
            for filho in elem:
                nome = _localname(filho.tag)
                if not nome:
                    continue
                grupos.setdefault((caminho, nome), []).append(filho)
                fila.append((filho, f"{caminho}/{nome}"))

        melhor: Optional[Tuple[str, str, List[ET.Element], Dict[str, str]]] = None
        for (caminho, nome), elementos in grupos.items():
            if len(elementos) < 2:
                continue  # "repetidos": um registro só não caracteriza tabela
            uniao: List[str] = []
            for elem in elementos[:5]:
                for campo in _campos_do_registro(elem):
                    if campo not in uniao:
                        uniao.append(campo)
            papeis = _mapeia_papeis(uniao, contexto.mapeamento)
            if not _qualifica(papeis):
                continue
            if melhor is None or len(elementos) > len(melhor[2]):
                melhor = (caminho, nome, elementos, papeis)

        if melhor is None:
            resultado.mapeamento_necessario = self._estrutura_para_mapeamento(grupos)
            resultado.avisos.append(
                "estrutura do XML não reconhecida automaticamente — mapeamento manual necessário."
            )
            return resultado

        caminho, nome, elementos, papeis = melhor
        eh_cartao = contexto.tipo_documento == "fatura_cartao"
        ocorrencias: Dict[Tuple[str, int, str, str, str], int] = {}

        for indice, elem in enumerate(elementos, start=1):
            ref = f"{caminho}/{nome}[{indice}]"
            campos = _campos_do_registro(elem)
            resultado.linhas_brutas.append(LinhaBruta(ordem=indice, origem_ref=ref, conteudo=campos))

            data = _para_data(campos.get(papeis["data"], ""))
            if data is None:
                resultado.erros.append(
                    {
                        "origem_ref": ref,
                        "campo": papeis["data"],
                        "mensagem": f"data inválida ou ausente: {campos.get(papeis['data'], '')!r}",
                    }
                )
                continue

            direcao_dc = (
                _direcao_do_texto(campos.get(papeis["direcao"], "")) if "direcao" in papeis else None
            )
            centavos: Optional[int] = None
            direcao: Optional[str] = None
            if "valor" in papeis:
                centavos = parse_centavos(campos.get(papeis["valor"]), moeda)
            if centavos is None and ("credito" in papeis or "debito" in papeis):
                credito = (
                    parse_centavos(campos.get(papeis["credito"], ""), moeda)
                    if "credito" in papeis
                    else None
                )
                debito = (
                    parse_centavos(campos.get(papeis["debito"], ""), moeda)
                    if "debito" in papeis
                    else None
                )
                if credito:
                    centavos, direcao = abs(credito), "credito"
                elif debito:
                    centavos, direcao = abs(debito), "debito"
            if centavos is None:
                resultado.erros.append(
                    {
                        "origem_ref": ref,
                        "campo": papeis.get("valor", "valor"),
                        "mensagem": "valor monetário inválido ou ausente no registro.",
                    }
                )
                continue
            if direcao is None:
                # Sinal negativo manda; positivo aceita a coluna D/C quando houver.
                direcao = "debito" if centavos < 0 else (direcao_dc or "credito")

            if eh_cartao:
                tipo = "compra" if direcao == "debito" else "estorno"
            else:
                tipo = "despesa" if direcao == "debito" else "receita"

            descricao = campos.get(papeis.get("descricao", ""), "") or ""
            documento = campos.get(papeis.get("documento", "")) or None
            contraparte = campos.get(papeis.get("contraparte", "")) or None
            saldo_apos = (
                parse_centavos(campos.get(papeis["saldo"], ""), moeda) if "saldo" in papeis else None
            )
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
                    tipo=tipo,
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
                    documento=documento,
                    contraparte=contraparte,
                    saldo_apos_centavos=saldo_apos,
                )
            )

        datas = [t.data_operacao for t in resultado.transacoes]
        resultado.periodo_inicio = min(datas) if datas else None
        resultado.periodo_fim = max(datas) if datas else None
        if not resultado.transacoes and resultado.erros:
            resultado.avisos.append("nenhum registro do XML pôde ser convertido em transação.")
        return resultado

    @staticmethod
    def _estrutura_para_mapeamento(
        grupos: Dict[Tuple[str, str], List[ET.Element]]
    ) -> Dict[str, Any]:
        """Descreve o que foi achado para o mapeador manual da UI."""
        caminhos: List[Dict[str, Any]] = []
        for (caminho, nome), elementos in grupos.items():
            uniao: List[str] = []
            for elem in elementos[:3]:
                for campo in _campos_do_registro(elem):
                    if campo not in uniao:
                        uniao.append(campo)
            caminhos.append(
                {"caminho": f"{caminho}/{nome}", "ocorrencias": len(elementos), "campos": uniao}
            )
        caminhos.sort(key=lambda item: (-item["ocorrencias"], item["caminho"]))
        amostra: List[Dict[str, str]] = []
        for info in caminhos:
            if info["ocorrencias"] >= 2 and info["campos"]:
                chave = tuple(info["caminho"].rsplit("/", 1))
                elementos = grupos.get((chave[0], chave[1]), []) if len(chave) == 2 else []
                amostra = [_campos_do_registro(e) for e in elementos[:5]]
                break
        return {
            "motivo": "nenhum conjunto de elementos repetidos com data e valor foi reconhecido",
            "caminhos": caminhos[:10],
            "amostra": amostra,
        }


ADAPTADORES = [AdaptadorXmlGenerico()]
