"""Parser da planilha de configuração de cartões e faturas ("planilha-config").

Reconhece DOIS layouts:

(a) **Dashboard de Faturas** — aba com a tabela de colunas
    ``Data | Dia | Mês | Ano | Cartão | Valor | Status`` (nomes podem variar de
    caixa; o cabeçalho é achado por sinônimos) e, opcionalmente, uma aba — em
    geral oculta, chamada "Calc" — com a lista de cartões (coluna "Cartões").
    Cada linha da tabela vira uma fatura: ``vence_em`` = Data, ``competencia``
    = AAAA-MM do vencimento, ``pago_centavos`` = coluna Valor (vazia = 0 — a
    planilha registra o PAGAMENTO, não o total da fatura) e status derivado por
    :func:`engine.extratos.modelos.deriva_status_fatura`. Os cartões saem só
    com ``nome`` e ``dia_vencimento`` (moda dos dias de vencimento das linhas
    daquele cartão); emissor/bandeira/final/limites/fechamento NÃO são
    inventados — ficam ``None`` e editáveis na UI.

(b) **Cadastro genérico** — colunas instituição/conta/cartão/emissor/bandeira/
    final/limite/fechamento/vencimento/conta pagadora (sinônimos pt-BR),
    quando existirem; mapeia o que houver.

No pipeline, o adaptador devolve ``ResultadoParse`` apenas com linhas brutas,
avisos e erros — NENHUMA transação é gerada por este formato. A importação de
verdade é feita por ``engine/extratos/config_planilha.py``, que chama
:func:`parse_config` direto (sem passar pelo pipeline de transações).

Conteúdo ruim nunca vira exceção: vira erro por linha em ``erros`` ou aviso.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from ...workbook import as_date
from ..detect import decodifica_texto, detectar
from ..dinheiro import parse_centavos
from ..modelos import Fatura, LinhaBruta, ResultadoParse, deriva_status_fatura
from .base import ContextoParse
from .csv_generico import linha_vazia, neutraliza_formula

#: Quantas linhas iniciais são vasculhadas em busca de cabeçalho.
LINHAS_BUSCA_CABECALHO = 30

#: Grade de uma aba: (título, [(número real da linha, células)]).
GradeAba = Tuple[str, List[Tuple[int, List[Any]]]]

#: Sinônimos (normalizados) do layout Dashboard de Faturas.
_CAMPOS_DASHBOARD: Dict[str, str] = {
    "data": "data", "data de vencimento": "data", "data vencimento": "data",
    "dia": "dia", "mes": "mes", "ano": "ano",
    "cartao": "cartao", "cartoes": "cartao", "nome do cartao": "cartao",
    "valor": "valor", "valor pago": "valor", "pago": "valor",
    "pagamento": "valor",
    "status": "status", "situacao": "status",
}

#: Sinônimos (normalizados) do layout de cadastro genérico.
_CAMPOS_CADASTRO: Dict[str, str] = {
    "instituicao": "instituicao", "banco": "instituicao",
    "conta": "conta", "nome da conta": "conta", "conta corrente": "conta",
    "cartao": "cartao", "nome do cartao": "cartao", "cartoes": "cartao",
    "emissor": "emissor", "emissor do cartao": "emissor",
    "bandeira": "bandeira",
    "final": "final", "final do cartao": "final", "final cartao": "final",
    "ultimos 4 digitos": "final", "4 ultimos digitos": "final",
    "limite": "limite", "limite total": "limite", "limite do cartao": "limite",
    "fechamento": "fechamento", "dia de fechamento": "fechamento",
    "dia fechamento": "fechamento", "dia do fechamento": "fechamento",
    "vencimento": "vencimento", "dia de vencimento": "vencimento",
    "dia vencimento": "vencimento", "dia do vencimento": "vencimento",
    "conta pagadora": "conta_pagadora", "conta de pagamento": "conta_pagadora",
    "conta para pagamento": "conta_pagadora",
    "tipo": "tipo", "tipo da conta": "tipo",
    "moeda": "moeda",
}

#: Rótulos que marcam a coluna com a lista de cartões (aba "Calc" e afins).
_ROTULOS_LISTA_CARTOES = ("cartoes", "cartao", "lista de cartoes")

_MESES: Dict[str, int] = {
    "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "marco": 3, "mar": 3,
    "abril": 4, "abr": 4, "maio": 5, "mai": 5, "junho": 6, "jun": 6,
    "julho": 7, "jul": 7, "agosto": 8, "ago": 8, "setembro": 9, "set": 9,
    "outubro": 10, "out": 10, "novembro": 11, "nov": 11, "dezembro": 12,
    "dez": 12,
}


# --------------------------------------------------------------------------
# Entidades extraídas (contrato com config_planilha.py)
# --------------------------------------------------------------------------


@dataclass
class CartaoConfig:
    """Cartão como descrito na planilha (nomes, nunca ids de banco)."""

    nome: str
    dia_vencimento: Optional[int] = None
    emissor: Optional[str] = None
    bandeira: Optional[str] = None
    final: Optional[str] = None  # só os 4 últimos dígitos
    dia_fechamento: Optional[int] = None
    limite_total_centavos: Optional[int] = None
    conta_pagadora: Optional[str] = None  # nome da conta, resolvido depois
    instituicao: Optional[str] = None  # nome da instituição, resolvido depois
    origem_ref: str = ""


@dataclass
class FaturaConfig:
    """Uma linha de fatura da planilha (cartão referenciado por nome)."""

    cartao: str
    vence_em: date
    competencia: str  # AAAA-MM do vencimento
    pago_centavos: int = 0
    status: str = "aberta"
    origem_ref: str = ""


@dataclass
class ContaConfig:
    """Conta bancária do cadastro genérico."""

    nome: str
    instituicao: Optional[str] = None
    tipo: str = "corrente"
    moeda: str = "BRL"
    origem_ref: str = ""


@dataclass
class ConfigExtraida:
    """Tudo que a planilha de configuração declara — nada toca o banco aqui."""

    cartoes: List[CartaoConfig] = field(default_factory=list)
    faturas: List[FaturaConfig] = field(default_factory=list)
    contas: List[ContaConfig] = field(default_factory=list)
    erros: List[Dict[str, Any]] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)
    mapeamento: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Utilitários de célula
# --------------------------------------------------------------------------


def _norm(texto: Any) -> str:
    """Minúsculas, sem acento, só letras/dígitos — para casar rótulos."""
    plano = unicodedata.normalize("NFKD", str(texto))
    plano = "".join(ch for ch in plano if not unicodedata.combining(ch))
    plano = re.sub(r"[^a-z0-9]+", " ", plano.lower())
    return re.sub(r"\s+", " ", plano).strip()


def _texto(valor: Any) -> Optional[str]:
    """Célula → texto em uma linha (``None`` quando vazia)."""
    if valor is None:
        return None
    if isinstance(valor, float) and valor.is_integer():
        texto = str(int(valor))
    else:
        texto = str(valor)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


def _dia_de(valor: Any) -> Tuple[Optional[int], bool]:
    """Célula → dia do mês. Devolve ``(dia, valido)``; vazia = ``(None, True)``."""
    if isinstance(valor, bool):
        return None, False
    texto = _texto(valor)
    if texto is None:
        return None, True
    if not re.fullmatch(r"\d{1,2}", texto):
        return None, False
    dia = int(texto)
    return (dia, True) if 1 <= dia <= 31 else (None, False)


def _mes_numero(valor: Any) -> Optional[int]:
    """Mês numérico ("6") ou por extenso pt-BR ("Junho"/"jun") → 1..12."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        mes = int(valor)
        return mes if 1 <= mes <= 12 else None
    chave = _norm(valor)
    if chave.isdigit():
        mes = int(chave)
        return mes if 1 <= mes <= 12 else None
    return _MESES.get(chave)


def _ano_numero(valor: Any) -> Optional[int]:
    texto = _texto(valor)
    if texto is None or not re.fullmatch(r"\d{4}", texto):
        return None
    return int(texto)


def _data_de_partes(dia_raw: Any, mes_raw: Any, ano_raw: Any) -> Optional[date]:
    """Reconstrói a data de vencimento pelas colunas Dia/Mês/Ano."""
    dia, dia_ok = _dia_de(dia_raw)
    mes = _mes_numero(mes_raw)
    ano = _ano_numero(ano_raw)
    if not (dia_ok and dia and mes and ano):
        return None
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _final_cartao(valor: Any) -> Optional[str]:
    """Mantém SOMENTE os 4 últimos dígitos — nunca o número completo."""
    texto = _texto(valor)
    if texto is None:
        return None
    digitos = re.sub(r"\D", "", texto)
    return digitos[-4:] if digitos else None


# --------------------------------------------------------------------------
# Carregamento das grades (xlsx e csv)
# --------------------------------------------------------------------------


def _carrega_grades(
    conteudo: bytes, nome: str
) -> Tuple[List[GradeAba], List[Dict[str, Any]], List[str]]:
    """Bytes → grades por aba. Erros de leitura viram itens, nunca exceção."""
    grades: List[GradeAba] = []
    erros: List[Dict[str, Any]] = []
    avisos: List[str] = []
    formato = detectar(conteudo, nome)

    if formato.formato == "xlsx":
        try:
            import openpyxl

            pasta = openpyxl.load_workbook(
                io.BytesIO(conteudo), read_only=True, data_only=True
            )
        except Exception as erro:  # noqa: BLE001 - arquivo ruim nunca derruba
            erros.append({
                "origem_ref": "arquivo",
                "mensagem": f"falha ao abrir a planilha XLSX: {erro}",
            })
            return grades, erros, avisos
        try:
            # Abas ocultas ENTRAM: a lista de cartões mora numa aba oculta.
            for aba in pasta.worksheets:
                grade: List[Tuple[int, List[Any]]] = []
                try:
                    for numero, linha in enumerate(
                        aba.iter_rows(values_only=True), start=1
                    ):
                        if not linha_vazia(linha):
                            grade.append((numero, list(linha)))
                except Exception as erro:  # noqa: BLE001 - aba ruim vira erro
                    erros.append({
                        "origem_ref": f"{aba.title}!L1",
                        "mensagem": f"falha ao ler a aba: {erro}",
                    })
                    continue
                if grade:
                    grades.append((aba.title, grade))
        finally:
            pasta.close()
        return grades, erros, avisos

    if formato.formato == "csv":
        texto: Optional[str] = None
        if formato.encoding:
            try:
                texto = conteudo.decode(formato.encoding)
            except (UnicodeDecodeError, LookupError):
                texto = None
        if texto is None:
            texto, _ = decodifica_texto(conteudo)
        if texto is None:
            erros.append({
                "origem_ref": "arquivo",
                "mensagem": "conteúdo binário ou encoding não reconhecido",
            })
            return grades, erros, avisos
        grade_csv: List[Tuple[int, List[Any]]] = []
        leitor = csv.reader(
            io.StringIO(texto, newline=""), delimiter=formato.delimitador or ";"
        )
        ultima_linha_fisica = 0
        try:
            for celulas in leitor:
                inicio = ultima_linha_fisica + 1
                ultima_linha_fisica = leitor.line_num
                if not linha_vazia(celulas):
                    grade_csv.append((inicio, list(celulas)))
        except csv.Error as erro:
            erros.append({
                "origem_ref": f"linha {ultima_linha_fisica + 1}",
                "mensagem": f"CSV malformado: {erro}",
            })
        if grade_csv:
            grades.append(("planilha", grade_csv))
        return grades, erros, avisos

    erros.append({
        "origem_ref": "arquivo",
        "mensagem": (
            f"formato '{formato.formato}' não suportado para planilha de "
            "configuração — envie XLSX ou CSV"
        ),
    })
    return grades, erros, avisos


# --------------------------------------------------------------------------
# Layout (a): Dashboard de Faturas
# --------------------------------------------------------------------------


def _acha_dashboard(
    grades: List[GradeAba],
) -> Optional[Tuple[str, List[Tuple[int, List[Any]]], int, Dict[int, str], Dict[int, str]]]:
    """Procura a aba/linha de cabeçalho da tabela de faturas.

    Devolve ``(aba, grade, posição do cabeçalho, {coluna: campo},
    {coluna: rótulo original})`` — exige Cartão + Valor e Data (ou Dia+Mês+Ano).
    """
    for titulo, grade in grades:
        for pos, (_, celulas) in enumerate(grade[:LINHAS_BUSCA_CABECALHO]):
            colunas: Dict[int, str] = {}
            rotulos: Dict[int, str] = {}
            for indice, celula in enumerate(celulas):
                if not isinstance(celula, str):
                    continue  # cabeçalho é texto
                campo = _CAMPOS_DASHBOARD.get(_norm(celula))
                if campo and campo not in colunas.values():
                    colunas[indice] = campo
                    rotulos[indice] = celula.strip()
            campos = set(colunas.values())
            if (
                "cartao" in campos
                and "valor" in campos
                and ("data" in campos or {"dia", "mes", "ano"} <= campos)
            ):
                return titulo, grade, pos, colunas, rotulos
    return None


def _acha_lista_cartoes(
    grades: List[GradeAba], excluir: str
) -> Tuple[Optional[str], List[str]]:
    """Acha a coluna "Cartões" fora da aba de faturas (aba "Calc" primeiro)."""
    candidatas = sorted(grades, key=lambda par: 0 if _norm(par[0]) == "calc" else 1)
    for titulo, grade in candidatas:
        if titulo == excluir:
            continue
        for pos, (_, celulas) in enumerate(grade[:LINHAS_BUSCA_CABECALHO]):
            for indice, celula in enumerate(celulas):
                if not isinstance(celula, str):
                    continue
                if _norm(celula) not in _ROTULOS_LISTA_CARTOES:
                    continue
                nomes: List[str] = []
                for _, linha in grade[pos + 1:]:
                    valor = _texto(linha[indice]) if indice < len(linha) else None
                    if valor:
                        nomes.append(valor)
                if nomes:
                    return titulo, nomes
    return None, []


def _parse_dashboard(
    titulo: str,
    grade: List[Tuple[int, List[Any]]],
    pos_cabecalho: int,
    colunas: Dict[int, str],
    config: ConfigExtraida,
    hoje: date,
) -> Tuple[Dict[str, str], List[str], Dict[str, List[int]]]:
    """Linhas da tabela → faturas. Devolve nomes/ordem/dias por cartão."""
    coluna_de = {campo: indice for indice, campo in colunas.items()}

    def celula(campo: str, celulas: List[Any]) -> Any:
        indice = coluna_de.get(campo)
        if indice is None or indice >= len(celulas):
            return None
        return celulas[indice]

    nomes: Dict[str, str] = {}
    ordem: List[str] = []
    dias: Dict[str, List[int]] = {}

    for numero, celulas in grade[pos_cabecalho + 1:]:
        ref = f"{titulo}!L{numero}"
        nome_cartao = _texto(celula("cartao", celulas)) or ""
        vence_em = as_date(celula("data", celulas))
        if vence_em is None:
            vence_em = _data_de_partes(
                celula("dia", celulas), celula("mes", celulas), celula("ano", celulas)
            )
        if not nome_cartao and vence_em is None:
            continue  # linha de resumo/rodapé fora da tabela — não é erro
        if not nome_cartao:
            config.erros.append({
                "origem_ref": ref, "campo": "cartao",
                "mensagem": "linha de fatura sem cartão — ignorada",
            })
            continue
        if vence_em is None:
            config.erros.append({
                "origem_ref": ref, "campo": "data",
                "mensagem": "data de vencimento inválida ou ausente — linha ignorada",
            })
            continue

        pago_centavos = 0
        bruto_valor = celula("valor", celulas)
        if bruto_valor is not None and str(bruto_valor).strip():
            centavos = parse_centavos(bruto_valor)
            if centavos is None:
                config.erros.append({
                    "origem_ref": ref, "campo": "valor",
                    "mensagem": "valor de pagamento não numérico — tratado como não pago",
                })
            elif centavos < 0:
                config.erros.append({
                    "origem_ref": ref, "campo": "valor",
                    "mensagem": "pagamento negativo — tratado como não pago",
                })
            else:
                pago_centavos = centavos

        competencia = f"{vence_em.year:04d}-{vence_em.month:02d}"
        if vence_em > hoje:
            # Regra da própria planilha: SE(Data>HOJE;"A vencer";...) — a data
            # vem ANTES do valor, então fatura futura fica "a vencer" (aberta)
            # mesmo com pagamento registrado na coluna Valor.
            status = "aberta"
        else:
            status = deriva_status_fatura(
                Fatura(
                    cartao_id=0,
                    competencia=competencia,
                    vence_em=vence_em,
                    pago_centavos=pago_centavos,
                ),
                hoje,
            )
        config.faturas.append(
            FaturaConfig(
                cartao=nome_cartao,
                vence_em=vence_em,
                competencia=competencia,
                pago_centavos=pago_centavos,
                status=status,
                origem_ref=ref,
            )
        )
        chave = _norm(nome_cartao)
        if chave not in nomes:
            nomes[chave] = nome_cartao
            ordem.append(chave)
        dias.setdefault(chave, []).append(vence_em.day)

    return nomes, ordem, dias


def _moda_dias(lista: List[int]) -> int:
    """Moda dos dias; empate resolve pelo MENOR dia (determinístico)."""
    contagem = Counter(lista)
    return max(contagem.items(), key=lambda par: (par[1], -par[0]))[0]


# --------------------------------------------------------------------------
# Layout (b): cadastro genérico
# --------------------------------------------------------------------------


def _acha_cadastro(
    grade: List[Tuple[int, List[Any]]],
) -> Optional[Tuple[int, Dict[int, str], Dict[int, str]]]:
    """Cabeçalho do cadastro: precisa de cartão ou conta + mais um campo."""
    for pos, (_, celulas) in enumerate(grade[:LINHAS_BUSCA_CABECALHO]):
        colunas: Dict[int, str] = {}
        rotulos: Dict[int, str] = {}
        for indice, celula in enumerate(celulas):
            if not isinstance(celula, str):
                continue
            campo = _CAMPOS_CADASTRO.get(_norm(celula))
            if campo and campo not in colunas.values():
                colunas[indice] = campo
                rotulos[indice] = celula.strip()
        campos = set(colunas.values())
        if ("cartao" in campos or "conta" in campos) and len(campos) >= 2:
            return pos, colunas, rotulos
    return None


def _parse_cadastro(
    titulo: str,
    grade: List[Tuple[int, List[Any]]],
    pos_cabecalho: int,
    colunas: Dict[int, str],
    config: ConfigExtraida,
) -> None:
    """Linhas do cadastro → cartões e contas (mapeia o que houver)."""
    coluna_de = {campo: indice for indice, campo in colunas.items()}

    def celula(campo: str, celulas: List[Any]) -> Any:
        indice = coluna_de.get(campo)
        if indice is None or indice >= len(celulas):
            return None
        return celulas[indice]

    def dia_ou_erro(campo: str, celulas: List[Any], ref: str) -> Optional[int]:
        bruto = celula(campo, celulas)
        dia, valido = _dia_de(bruto)
        if not valido:
            config.erros.append({
                "origem_ref": ref, "campo": campo,
                "mensagem": f"dia inválido '{bruto}' — precisa ser 1..31",
            })
        return dia

    for numero, celulas in grade[pos_cabecalho + 1:]:
        ref = f"{titulo}!L{numero}"
        nome_cartao = _texto(celula("cartao", celulas))
        nome_conta = _texto(celula("conta", celulas))
        if nome_cartao:
            limite_centavos: Optional[int] = None
            bruto_limite = celula("limite", celulas)
            if bruto_limite is not None and str(bruto_limite).strip():
                limite_centavos = parse_centavos(bruto_limite)
                if limite_centavos is None or limite_centavos < 0:
                    config.erros.append({
                        "origem_ref": ref, "campo": "limite",
                        "mensagem": "limite não numérico ou negativo — ignorado",
                    })
                    limite_centavos = None
            config.cartoes.append(
                CartaoConfig(
                    nome=nome_cartao,
                    dia_vencimento=dia_ou_erro("vencimento", celulas, ref),
                    emissor=_texto(celula("emissor", celulas)),
                    bandeira=_texto(celula("bandeira", celulas)),
                    final=_final_cartao(celula("final", celulas)),
                    dia_fechamento=dia_ou_erro("fechamento", celulas, ref),
                    limite_total_centavos=limite_centavos,
                    conta_pagadora=_texto(celula("conta_pagadora", celulas)),
                    instituicao=_texto(celula("instituicao", celulas)),
                    origem_ref=ref,
                )
            )
        elif nome_conta:
            config.contas.append(
                ContaConfig(
                    nome=nome_conta,
                    instituicao=_texto(celula("instituicao", celulas)),
                    tipo=(_texto(celula("tipo", celulas)) or "corrente").lower(),
                    moeda=(_texto(celula("moeda", celulas)) or "BRL").upper(),
                    origem_ref=ref,
                )
            )
        # linha sem cartão e sem conta: nada a cadastrar — segue o baile


# --------------------------------------------------------------------------
# Extração completa
# --------------------------------------------------------------------------


def _extrai_config(
    grades: List[GradeAba],
    erros_carga: List[Dict[str, Any]],
    avisos_carga: List[str],
    hoje: date,
) -> ConfigExtraida:
    config = ConfigExtraida()
    config.erros.extend(erros_carga)
    config.avisos.extend(avisos_carga)
    if not grades:
        if not config.erros:
            config.erros.append({
                "origem_ref": "arquivo",
                "mensagem": "planilha sem nenhuma linha com conteúdo",
            })
        return config

    achado = _acha_dashboard(grades)
    if achado is not None:
        titulo, grade, pos, colunas, rotulos = achado
        nomes, ordem, dias = _parse_dashboard(titulo, grade, pos, colunas, config, hoje)
        aba_cartoes, lista_calc = _acha_lista_cartoes(grades, excluir=titulo)

        nomes_finais: Dict[str, str] = {}
        ordem_final: List[str] = []
        for nome in lista_calc:  # a lista oficial de cartões vem primeiro
            chave = _norm(nome)
            if chave and chave not in nomes_finais:
                nomes_finais[chave] = nome
                ordem_final.append(chave)
        for chave in ordem:  # cartões que só aparecem nas linhas de fatura
            if chave not in nomes_finais:
                nomes_finais[chave] = nomes[chave]
                ordem_final.append(chave)
                config.avisos.append(
                    f"cartão '{nomes[chave]}' aparece nas faturas mas não na "
                    "lista de cartões"
                )

        for chave in ordem_final:
            lista_dias = dias.get(chave, [])
            dia_vencimento = _moda_dias(lista_dias) if lista_dias else None
            if not lista_dias:
                config.avisos.append(
                    f"cartão '{nomes_finais[chave]}' sem faturas na planilha — "
                    "dia de vencimento desconhecido"
                )
            # Só nome + dia de vencimento: a planilha NÃO informa emissor,
            # bandeira, final, limites nem fechamento — ninguém inventa.
            config.cartoes.append(
                CartaoConfig(nome=nomes_finais[chave], dia_vencimento=dia_vencimento)
            )

        config.mapeamento = {
            "layout": "dashboard_faturas",
            "aba_faturas": titulo,
            "aba_cartoes": aba_cartoes,
            "colunas": {campo: rotulos[indice] for indice, campo in colunas.items()},
        }
        return config

    # Fallback: cadastro genérico, aba a aba.
    abas_mapeadas: Dict[str, Dict[str, str]] = {}
    for titulo, grade in grades:
        achado_cadastro = _acha_cadastro(grade)
        if achado_cadastro is None:
            continue
        pos, colunas, rotulos = achado_cadastro
        _parse_cadastro(titulo, grade, pos, colunas, config)
        abas_mapeadas[titulo] = {
            campo: rotulos[indice] for indice, campo in colunas.items()
        }
    if abas_mapeadas:
        config.mapeamento = {"layout": "cadastro", "abas": abas_mapeadas}
    else:
        config.erros.append({
            "origem_ref": "arquivo",
            "mensagem": (
                "layout de configuração não reconhecido — esperado a tabela de "
                "faturas (Data/Cartão/Valor) ou um cadastro "
                "(instituição/conta/cartão)"
            ),
        })

    _deduplica(config)
    return config


def _deduplica(config: ConfigExtraida) -> None:
    """Nomes repetidos no cadastro: a primeira ocorrência prevalece."""
    vistos_cartao: Dict[str, CartaoConfig] = {}
    ordem_cartao: List[str] = []
    for cartao in config.cartoes:
        chave = _norm(cartao.nome)
        if chave in vistos_cartao:
            config.avisos.append(
                f"cartão '{cartao.nome}' repetido no cadastro ({cartao.origem_ref})"
                " — primeira ocorrência prevalece"
            )
            continue
        vistos_cartao[chave] = cartao
        ordem_cartao.append(chave)
    config.cartoes = [vistos_cartao[chave] for chave in ordem_cartao]

    vistas_conta: Dict[str, ContaConfig] = {}
    ordem_conta: List[str] = []
    for conta in config.contas:
        chave = _norm(conta.nome)
        if chave in vistas_conta:
            config.avisos.append(
                f"conta '{conta.nome}' repetida no cadastro ({conta.origem_ref})"
                " — primeira ocorrência prevalece"
            )
            continue
        vistas_conta[chave] = conta
        ordem_conta.append(chave)
    config.contas = [vistas_conta[chave] for chave in ordem_conta]


def parse_config(
    conteudo: bytes, nome: str = "", *, hoje: Optional[date] = None
) -> ConfigExtraida:
    """Extrai cartões/faturas/contas da planilha de configuração.

    Esta é a porta de entrada usada por ``config_planilha.previa_config`` e
    ``config_planilha.confirmar_config`` — NÃO passa pelo pipeline de
    transações. ``hoje`` permite derivar status de fatura com data de
    referência fixa (testes e reprocessamentos determinísticos).
    """
    grades, erros, avisos = _carrega_grades(conteudo, nome)
    return _extrai_config(grades, erros, avisos, hoje or date.today())


# --------------------------------------------------------------------------
# Adaptador do pipeline (linhas brutas + avisos; nenhuma transação)
# --------------------------------------------------------------------------


class AdaptadorPlanilhaConfig:
    """Planilha de configuração de cartões/faturas (Dashboard ou cadastro)."""

    nome = "planilha-config"
    versao = "1"

    def detecta(self, contexto: ContextoParse) -> float:
        if contexto.tipo_documento != "planilha_config":
            return 0.0
        return 0.9 if contexto.formato.formato in ("xlsx", "csv") else 0.0

    def parse(self, contexto: ContextoParse) -> ResultadoParse:
        resultado = ResultadoParse(adaptador=self.nome, moeda=contexto.moeda)
        grades, erros_carga, avisos_carga = _carrega_grades(
            contexto.conteudo, contexto.nome_original
        )

        # Espelho auditável: toda linha útil de toda aba vira linha bruta.
        for titulo, grade in grades:
            for numero, celulas in grade:
                resultado.linhas_brutas.append(
                    LinhaBruta(
                        ordem=len(resultado.linhas_brutas) + 1,
                        origem_ref=f"{titulo}!L{numero}",
                        conteudo={"celulas": [neutraliza_formula(c) for c in celulas]},
                    )
                )

        config = _extrai_config(grades, erros_carga, avisos_carga, date.today())
        resultado.avisos.extend(config.avisos)
        resultado.erros.extend(config.erros)
        if config.faturas:
            datas = [fatura.vence_em for fatura in config.faturas]
            resultado.periodo_inicio = min(datas)
            resultado.periodo_fim = max(datas)
        resultado.avisos.append(
            f"planilha de configuração reconhecida: {len(config.cartoes)} "
            f"cartão(ões), {len(config.faturas)} fatura(s), "
            f"{len(config.contas)} conta(s) — nenhuma transação é gerada; "
            "confirme a importação pela tela de Config"
        )
        return resultado


ADAPTADORES = [AdaptadorPlanilhaConfig()]
