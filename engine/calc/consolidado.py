"""Patrimônio consolidado (abas ``Posicao Consolidada`` e ``Dolarizado``).

Reproduz, em Python puro, o que a planilha fazia com fórmulas:

* total do patrimônio somando todas as contas/instituições;
* participação (``share``) de cada conta sobre o total;
* agregação por **categoria** (Investimentos BR, Internacional, Cripto, Caixa,
  Reserva) e por **liquidez** (Imediata (D0), D+2, D+2 (câmbio), Reserva),
  ambas ordenadas do maior valor para o menor;
* carteira no exterior em US$ — valor, custo, resultado e resultado percentual
  por posição, subtotais por corretora e conversão para R$ pelo câmbio da aba.

As contas da aba consolidada **já incluem** o exterior convertido em reais
(``Binance — criptomoedas`` = US$ 4.643,24 × câmbio, por exemplo). Por isso o
total consolidado é a soma pura das contas: somar de novo o bloco em US$
duplicaria o patrimônio.

Módulo **puro**: entram dataclasses de ``engine.models``, saem dataclasses de
``engine.models``. Sem I/O, sem rede, sem openpyxl.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import (
    ConsolidatedAccount,
    ConsolidatedResult,
    ForeignPosition,
    WorkbookInputs,
)

# Rótulos usados quando a planilha deixa o campo em branco.
CATEGORIA_PADRAO = "Outros"
LIQUIDEZ_PADRAO = "Não informada"
CORRETORA_PADRAO = "Exterior"

_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")
_NAO_ALFANUM = re.compile(r"[^a-z0-9]+")
_ESPACOS = re.compile(r"\s+")


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _numero(valor: object) -> float:
    """Converte para ``float`` tratando ``None``, texto inválido, NaN e infinito."""
    # Booleano num campo numérico é lixo de planilha: virar "1,00" inventaria
    # patrimônio, então tratamos como ausência de valor.
    if valor is None or isinstance(valor, bool):
        return 0.0
    try:
        numero = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    # NaN não é igual a si mesmo; infinito envenenaria todas as somas.
    if numero != numero or numero in (float("inf"), float("-inf")):
        return 0.0
    return numero


def _texto(valor: object) -> str:
    return "" if valor is None else str(valor).strip()


def _chave(rotulo: object) -> str:
    """Chave de agrupamento: minúsculas, sem acento e sem pontuação.

    Faz ``"D+2 (câmbio)"`` casar com ``"D+2 (cambio)"`` sem misturar com
    ``"D+2"`` — o ``+`` e os parênteses viram espaço, o resto permanece.
    """
    limpo = _texto(rotulo).lower().translate(_ACENTOS)
    return _ESPACOS.sub(" ", _NAO_ALFANUM.sub(" ", limpo)).strip()


def _divide(numerador: float, denominador: float) -> float:
    """Divisão que devolve 0.0 em vez de estourar quando o denominador é zero."""
    if not denominador:
        return 0.0
    resultado = numerador / denominador
    if resultado != resultado or resultado in (float("inf"), float("-inf")):
        return 0.0
    return resultado


def _limpar(itens: Optional[Iterable[Any]]) -> List[Any]:
    """Descarta ``None`` de uma lista que pode ela própria ser ``None``."""
    return [item for item in (itens or ()) if item is not None]


# --------------------------------------------------------------------------
# Agregação por rótulo (categoria, liquidez, corretora)
# --------------------------------------------------------------------------


def _agrupar(pares: Sequence[Tuple[str, float]]) -> List[Tuple[str, float, int]]:
    """Soma valores por rótulo normalizado, do maior valor para o menor.

    Devolve ``(rótulo exibido, soma, quantidade de itens)``. O rótulo exibido é
    o da primeira ocorrência — a planilha escreve o nome bonito, com acento.
    """
    acumulado: Dict[str, List[float]] = {}
    rotulos: Dict[str, str] = {}
    for rotulo, valor in pares:
        chave = _chave(rotulo)
        if chave not in acumulado:
            acumulado[chave] = [0.0, 0.0]
            rotulos[chave] = rotulo
        acumulado[chave][0] += valor
        acumulado[chave][1] += 1
    grupos = [
        (rotulos[chave], soma, int(quantidade))
        for chave, (soma, quantidade) in acumulado.items()
    ]
    # ``sorted`` é estável: grupos empatados mantêm a ordem da planilha.
    grupos.sort(key=lambda item: item[1], reverse=True)
    return grupos


def _grupos_para_dicts(
    grupos: Sequence[Tuple[str, float, int]], total: float, campo: str
) -> List[Dict[str, Any]]:
    """Formata os grupos como dicts prontos para JSON e para os gráficos.

    Além do campo específico (``category``/``liquidity``) sai um ``label``
    genérico, para o construtor de gráficos não precisar saber de qual eixo veio.
    """
    return [
        {
            campo: rotulo,
            "label": rotulo,
            "value": soma,
            "share": _divide(soma, total),
            "accounts": quantidade,
        }
        for rotulo, soma, quantidade in grupos
    ]


# --------------------------------------------------------------------------
# Contas (aba Posicao Consolidada)
# --------------------------------------------------------------------------


def _contas(
    contas: Sequence[ConsolidatedAccount],
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, float]], List[Tuple[str, float]], float]:
    """Normaliza as contas e devolve (dicts, pares categoria, pares liquidez, total)."""
    linhas: List[Dict[str, Any]] = []
    por_categoria: List[Tuple[str, float]] = []
    por_liquidez: List[Tuple[str, float]] = []
    total = 0.0

    for conta in contas:
        valor = _numero(getattr(conta, "value", 0.0))
        instituicao = _texto(getattr(conta, "institution", ""))
        categoria = _texto(getattr(conta, "category", "")) or CATEGORIA_PADRAO
        liquidez = _texto(getattr(conta, "liquidity", "")) or LIQUIDEZ_PADRAO
        total += valor
        por_categoria.append((categoria, valor))
        por_liquidez.append((liquidez, valor))
        linhas.append(
            {
                "institution": instituicao,
                "category": categoria,
                "liquidity": liquidez,
                "value": valor,
                "share": 0.0,  # preenchido abaixo, quando o total já é conhecido
            }
        )

    for linha in linhas:
        linha["share"] = _divide(float(linha["value"]), total)
    # Estável: contas de mesmo valor mantêm a ordem da planilha.
    linhas.sort(key=lambda item: item["value"], reverse=True)
    return linhas, por_categoria, por_liquidez, total


# --------------------------------------------------------------------------
# Exterior (aba Dolarizado)
# --------------------------------------------------------------------------


def _exterior(
    posicoes: Sequence[ForeignPosition], usd_brl: float
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], float, float]:
    """Normaliza o bloco em US$ e devolve (posições, subtotais, total, resultado).

    O valor de cada posição é recalculado como ``quantidade × preço`` em vez de
    confiar no que estava na célula: é o que a planilha fazia, e sobrevive a
    campos nulos ou textuais.
    """
    linhas: List[Dict[str, Any]] = []
    pares_corretora: List[Tuple[str, float]] = []
    custos: Dict[str, float] = {}
    total_usd = 0.0
    total_resultado = 0.0

    for posicao in posicoes:
        quantidade = _numero(getattr(posicao, "quantity", 0.0))
        preco = _numero(getattr(posicao, "price_usd", 0.0))
        custo = _numero(getattr(posicao, "cost_usd", 0.0))
        valor = quantidade * preco
        resultado = valor - custo
        corretora = _texto(getattr(posicao, "broker", "")) or CORRETORA_PADRAO
        ticker = _texto(getattr(posicao, "ticker", ""))

        total_usd += valor
        total_resultado += resultado
        pares_corretora.append((corretora, valor))
        chave = _chave(corretora)
        custos[chave] = custos.get(chave, 0.0) + custo

        linhas.append(
            {
                "broker": corretora,
                "ticker": ticker,
                "description": _texto(getattr(posicao, "description", "")) or ticker,
                "quantity": quantidade,
                "price_usd": preco,
                "value_usd": valor,
                "cost_usd": custo,
                "result_usd": resultado,
                "result_percent": _divide(resultado, custo),
                "share": 0.0,  # preenchido abaixo, com o total já conhecido
                "value_brl": valor * usd_brl,
            }
        )

    for linha in linhas:
        linha["share"] = _divide(float(linha["value_usd"]), total_usd)
    # Estável: posições de mesmo valor mantêm a ordem da planilha.
    linhas.sort(key=lambda item: item["value_usd"], reverse=True)

    subtotais: List[Dict[str, Any]] = []
    for corretora, soma, quantidade in _agrupar(pares_corretora):
        custo = custos.get(_chave(corretora), 0.0)
        resultado = soma - custo
        subtotais.append(
            {
                "broker": corretora,
                "label": corretora,
                "value_usd": soma,
                "value_brl": soma * usd_brl,
                "cost_usd": custo,
                "result_usd": resultado,
                "result_percent": _divide(resultado, custo),
                "share": _divide(soma, total_usd),
                "positions": quantidade,
            }
        )

    return linhas, subtotais, total_usd, total_resultado


# --------------------------------------------------------------------------
# Entrada pública
# --------------------------------------------------------------------------


def compute_consolidated(inputs: WorkbookInputs) -> ConsolidatedResult:
    """Consolida o patrimônio (contas em R$ + carteira no exterior em US$).

    Devolve sempre um :class:`ConsolidatedResult` válido: planilha sem contas,
    sem exterior ou sem câmbio produz zeros e listas vazias, nunca exceção.
    Sem câmbio (``usd_brl`` ausente ou zero) os campos em R$ do exterior ficam
    em 0,00 — o bloco em US$ continua íntegro.
    """
    contas: List[ConsolidatedAccount] = _limpar(
        getattr(inputs, "accounts", None) if inputs is not None else None
    )
    posicoes: List[ForeignPosition] = _limpar(
        getattr(inputs, "foreign", None) if inputs is not None else None
    )
    # Câmbio negativo é erro de digitação; tratamos como ausente.
    usd_brl = _numero(getattr(inputs, "usd_brl", 0.0) if inputs is not None else 0.0)
    if usd_brl < 0:
        usd_brl = 0.0

    linhas, pares_categoria, pares_liquidez, total = _contas(contas)
    exterior, por_corretora, total_usd, resultado_usd = _exterior(posicoes, usd_brl)

    return ConsolidatedResult(
        total=total,
        accounts=linhas,
        by_category=_grupos_para_dicts(_agrupar(pares_categoria), total, "category"),
        by_liquidity=_grupos_para_dicts(_agrupar(pares_liquidez), total, "liquidity"),
        usd_brl=usd_brl,
        foreign_total_usd=total_usd,
        foreign_total_brl=total_usd * usd_brl,
        foreign=exterior,
        foreign_by_broker=por_corretora,
        foreign_result_usd=resultado_usd,
    )
