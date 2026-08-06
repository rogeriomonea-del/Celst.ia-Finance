"""Cálculo da carteira B3 (aba ``Posicao_B3``).

Reproduz, em Python puro, o que a planilha fazia com fórmulas:

* valor de mercado de cada posição (``quantidade × preço``);
* peso de cada posição e de cada classe sobre o total da carteira;
* proventos recebidos por ticker (equivalente ao ``SUMIF`` da aba Proventos);
* variação de 12 meses ponderada pelas classes listadas.

Módulo **puro**: entram dataclasses de ``engine.models``, saem dataclasses de
``engine.models``. Sem I/O, sem rede, sem openpyxl.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import (
    ClassSummary,
    PortfolioResult,
    Position,
    PositionResult,
    Provento,
    WorkbookInputs,
)

# Classes negociadas em bolsa: só elas entram na variação ponderada de 12 meses.
# Renda Fixa privada e Tesouro Direto não têm cotação diária comparável, então
# ficam de fora tanto do numerador quanto do denominador.
CLASSES_LISTADAS: Tuple[str, ...] = (
    "Ações",
    "FIIs",
    "ETFs",
    "BDRs",
    "Fundos listados",
)

_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")
_NAO_ALFANUM = re.compile(r"[^a-z0-9]+")
_ESPACOS = re.compile(r"\s+")


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _numero(valor: object) -> float:
    """Converte para ``float`` tratando ``None``, texto inválido, NaN e infinito."""
    # Booleano numa coluna numérica é lixo de planilha: virar "1 cota" inventaria
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


def _opcional(valor: Optional[float]) -> Optional[float]:
    """Mantém ``None`` como ausência de dado, mas descarta NaN/infinito."""
    if valor is None:
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if numero != numero or numero in (float("inf"), float("-inf")):
        return None
    return numero


def _texto(valor: object) -> str:
    return "" if valor is None else str(valor).strip()


def _chave_ticker(ticker: object) -> str:
    """Chave de casamento de ticker: maiúsculas e sem espaço algum.

    ``" bbas3 "``, ``"BBAS3"`` e ``"bbas 3"`` casam entre si — é o que se espera
    de uma planilha preenchida à mão.
    """
    return _ESPACOS.sub("", _texto(ticker)).upper()


def _chave_classe(classe: object) -> str:
    """Chave de comparação de classe: minúsculas, sem acento e sem pontuação."""
    limpo = _texto(classe).lower().translate(_ACENTOS)
    return _ESPACOS.sub(" ", _NAO_ALFANUM.sub(" ", limpo)).strip()


_CLASSES_LISTADAS_CHAVES = frozenset(_chave_classe(c) for c in CLASSES_LISTADAS)


def _valor_de_mercado(posicao: Position) -> float:
    """``quantidade × preço``, imune a campos nulos ou textuais."""
    return _numero(getattr(posicao, "quantity", 0.0)) * _numero(getattr(posicao, "price", 0.0))


def _divide(numerador: float, denominador: float) -> float:
    """Divisão que devolve 0.0 em vez de estourar quando o denominador é zero."""
    if not denominador:
        return 0.0
    resultado = numerador / denominador
    if resultado != resultado or resultado in (float("inf"), float("-inf")):
        return 0.0
    return resultado


# --------------------------------------------------------------------------
# Proventos por ticker (SUMIF da planilha)
# --------------------------------------------------------------------------


def _agrupar_proventos(proventos: Optional[Iterable[Provento]]) -> Dict[str, float]:
    """Soma os proventos por ticker normalizado, preservando a ordem de chegada.

    Equivale a ``SUMIF(Proventos!ticker; posição; Proventos!valor)`` aplicado a
    todos os tickers de uma vez. Lançamentos sem ticker (CRA/CRI genéricos, por
    exemplo) ficam de fora — não há posição a que atribuí-los.
    """
    somas: Dict[str, float] = {}
    for provento in proventos or ():
        if provento is None:
            continue
        chave = _chave_ticker(getattr(provento, "ticker", ""))
        if not chave:
            continue
        somas[chave] = somas.get(chave, 0.0) + _numero(getattr(provento, "amount", 0.0))
    return somas


# --------------------------------------------------------------------------
# Variação ponderada de 12 meses
# --------------------------------------------------------------------------


def _var_12m_ponderada(posicoes: Sequence[Position], valores: Sequence[float]) -> float:
    """Média da ``var_12m`` ponderada pelo valor de mercado.

    Só entram as classes listadas (:data:`CLASSES_LISTADAS`) e, dentro delas, só
    as posições que realmente têm ``var_12m`` — o peso é renormalizado sobre esse
    subconjunto, e não sobre a carteira inteira.

    Se nenhuma classe listada for reconhecida (planilha genérica, classes com
    outros nomes), cai para *todas* as posições com ``var_12m``, para não
    devolver 0.0 quando existe informação disponível.
    """
    listadas: List[Tuple[float, float]] = []
    quaisquer: List[Tuple[float, float]] = []
    for posicao, valor in zip(posicoes, valores):
        variacao = _opcional(getattr(posicao, "var_12m", None))
        if variacao is None:
            continue
        quaisquer.append((valor, variacao))
        if _chave_classe(getattr(posicao, "asset_class", "")) in _CLASSES_LISTADAS_CHAVES:
            listadas.append((valor, variacao))

    selecionadas = listadas or quaisquer
    denominador = sum(valor for valor, _ in selecionadas)
    numerador = sum(valor * variacao for valor, variacao in selecionadas)
    return _divide(numerador, denominador)


# --------------------------------------------------------------------------
# Resumo por classe
# --------------------------------------------------------------------------


def _resumo_por_classe(
    posicoes: Sequence[Position], valores: Sequence[float], total: float
) -> List[ClassSummary]:
    """Agrega valor, peso e contagem por classe, do maior valor para o menor."""
    acumulado: Dict[str, List[float]] = {}
    rotulos: Dict[str, str] = {}
    for posicao, valor in zip(posicoes, valores):
        rotulo = _texto(getattr(posicao, "asset_class", "")) or "Outros"
        chave = _chave_classe(rotulo)
        if chave not in acumulado:
            acumulado[chave] = [0.0, 0.0]
            rotulos[chave] = rotulo
        acumulado[chave][0] += valor
        acumulado[chave][1] += 1

    resumo = [
        ClassSummary(
            asset_class=rotulos[chave],
            value=soma,
            weight=_divide(soma, total),
            positions=int(quantidade),
        )
        for chave, (soma, quantidade) in acumulado.items()
    ]
    # ``sorted`` é estável: classes empatadas mantêm a ordem da planilha.
    resumo.sort(key=lambda item: item.value, reverse=True)
    return resumo


# --------------------------------------------------------------------------
# Entrada pública
# --------------------------------------------------------------------------


def compute_portfolio(inputs: WorkbookInputs) -> PortfolioResult:
    """Calcula a carteira consolidada a partir das posições e dos proventos.

    Devolve sempre um :class:`PortfolioResult` válido: carteira vazia, preços
    zerados ou campos nulos produzem zeros e listas vazias, nunca exceção.
    """
    posicoes: List[Position] = [
        p for p in (getattr(inputs, "positions", None) or ()) if p is not None
    ]
    proventos = getattr(inputs, "proventos", None) if inputs is not None else None

    valores = [_valor_de_mercado(p) for p in posicoes]
    total = sum(valores)

    somas_proventos = _agrupar_proventos(proventos)
    # Só contam os tickers que existem na carteira — o que o SUMIF da planilha
    # faz ao varrer a coluna de posições. Somamos as chaves distintas (e não o
    # valor posição a posição) para que um mesmo ticker em duas corretoras não
    # duplique o total.
    tickers_carteira = {_chave_ticker(getattr(p, "ticker", "")) for p in posicoes}
    tickers_carteira.discard("")
    total_proventos = sum(
        valor for chave, valor in somas_proventos.items() if chave in tickers_carteira
    )

    resultados: List[PositionResult] = []
    for posicao, valor in zip(posicoes, valores):
        chave = _chave_ticker(getattr(posicao, "ticker", ""))
        resultados.append(
            PositionResult(
                ticker=_texto(getattr(posicao, "ticker", "")),
                name=_texto(getattr(posicao, "name", "")) or _texto(getattr(posicao, "ticker", "")),
                asset_class=_texto(getattr(posicao, "asset_class", "")) or "Outros",
                quantity=_numero(getattr(posicao, "quantity", 0.0)),
                price=_numero(getattr(posicao, "price", 0.0)),
                market_value=valor,
                portfolio_weight=_divide(valor, total),
                proventos_period=somas_proventos.get(chave, 0.0) if chave else 0.0,
                var_12m=_opcional(getattr(posicao, "var_12m", None)),
                price_earnings=_opcional(getattr(posicao, "price_earnings", None)),
                price_to_book=_opcional(getattr(posicao, "price_to_book", None)),
                dividend_yield=_opcional(getattr(posicao, "dividend_yield", None)),
                technical_signal=_texto(getattr(posicao, "technical_signal", "")),
                fundamental_read=_texto(getattr(posicao, "fundamental_read", "")),
                note=_texto(getattr(posicao, "note", "")),
            )
        )
    # Estável: posições de mesmo valor mantêm a ordem original da planilha.
    resultados.sort(key=lambda item: item.market_value, reverse=True)

    return PortfolioResult(
        total_value=total,
        total_proventos=total_proventos,
        positions_count=len(posicoes),
        weighted_var_12m=_var_12m_ponderada(posicoes, valores),
        positions=resultados,
        by_class=_resumo_por_classe(posicoes, valores, total),
    )
