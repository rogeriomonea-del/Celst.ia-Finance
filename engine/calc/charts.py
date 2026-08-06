"""Séries prontas para os gráficos do front-end (Recharts).

Reproduz, em Python puro, os 9 gráficos que a planilha desenhava a partir das
abas ``Dashboard``, ``Proventos``, ``Fluxo de Caixa`` e ``Posicao Consolidada``.
Cada gráfico vira um :class:`~engine.models.ChartSeries` com **id fixo** — o
front-end procura os gráficos por esse id, então nenhum deles pode mudar:

===========================  ======  ==================================
id                           tipo    origem
===========================  ======  ==================================
``alocacao-classe``          donut   ``portfolio.by_class``
``top-posicoes``             bar     10 maiores de ``portfolio.positions``
``melhores-piores``          bar     ``var_12m`` (5 melhores + 5 piores)
``maiores-proventos``        bar     ``proventos.top`` (8 primeiros)
``alocacao-patrimonio``      donut   ``consolidated.by_category``
``fluxo-vs-aporte``          bar     12 últimos de ``cashflow.monthly``
``liquidez``                 bar     ``consolidated.by_liquidity``
``evolucao-simulador``       line    ``simulation.series``
``projecao-2030``            area    ``projection.series``
===========================  ======  ==================================

Convenções de saída
-------------------
* ``x_key`` é sempre ``"label"``: o eixo X é o rótulo já formatado em pt-BR.
* ``series`` lista **o que é plotado** (``{"key", "label"}``); ``data`` pode
  trazer campos extras (``share``, ``month``, ``asset_class``…) para tooltips.
* ``value_format`` é ``"percent"`` só em ``melhores-piores`` (fração: 0,12 =
  +12%); nos demais é ``"currency"`` (reais).
* Gráficos de barras **categóricos** saem do maior valor para o menor.
  ``fluxo-vs-aporte`` é a exceção deliberada: é uma série temporal, então
  mantém a ordem cronológica (ordenar por valor destruiria a leitura do mês).
* ``melhores-piores`` sai do melhor para o pior, sem repetir posição quando
  há menos de 10 ativos com ``var_12m``.

Módulo **puro**: entram dataclasses de ``engine.models``, saem dataclasses de
``engine.models``. Sem I/O, sem rede, sem openpyxl. Qualquer argumento pode ser
``None`` — o gráfico correspondente simplesmente não é gerado.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import (
    CashflowResult,
    ChartSeries,
    ConsolidatedResult,
    PortfolioResult,
    ProjectionResult,
    ProventosResult,
    RebalanceResult,
    ScenarioResult,
    SimulationResult,
)

#: Ids dos gráficos, na ordem em que ``build_charts`` os devolve.
CHART_IDS: Tuple[str, ...] = (
    "alocacao-classe",
    "top-posicoes",
    "melhores-piores",
    "maiores-proventos",
    "alocacao-patrimonio",
    "fluxo-vs-aporte",
    "liquidez",
    "evolucao-simulador",
    "projecao-2030",
)

#: Quantas posições entram em ``top-posicoes``.
TOP_POSICOES = 10
#: Quantos ativos entram em ``maiores-proventos``.
TOP_PROVENTOS = 8
#: Quantos ativos de cada ponta entram em ``melhores-piores``.
EXTREMOS_VAR_12M = 5
#: Quantos meses entram em ``fluxo-vs-aporte``.
MESES_FLUXO = 12

_MESES_PT: Tuple[str, ...] = (
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _numero(valor: Any) -> float:
    """Converte para ``float`` tratando ``None``, texto, NaN e infinito."""
    if valor is None or isinstance(valor, bool):
        return 0.0
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return 0.0
    if numero != numero or numero in (float("inf"), float("-inf")):
        return 0.0
    return numero


def _opcional(valor: Any) -> Optional[float]:
    """Como ``_numero``, mas preserva a diferença entre "zero" e "sem dado"."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if numero != numero or numero in (float("inf"), float("-inf")):
        return None
    return numero


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def _divide(numerador: float, denominador: float) -> float:
    """Divisão que devolve 0.0 em vez de estourar quando o denominador é zero."""
    if not denominador:
        return 0.0
    resultado = numerador / denominador
    if resultado != resultado or resultado in (float("inf"), float("-inf")):
        return 0.0
    return resultado


def _itens(fonte: Any, atributo: str) -> List[Any]:
    """Lista de ``fonte.atributo`` sem ``None``; ``[]`` se algo faltar."""
    if fonte is None:
        return []
    valores = getattr(fonte, atributo, None)
    if not isinstance(valores, (list, tuple)):
        return []
    return [item for item in valores if item is not None]


def _campo(item: Any, *nomes: str) -> Any:
    """Lê um campo de dataclass **ou** de dict — o motor devolve os dois."""
    for nome in nomes:
        if isinstance(item, dict):
            if nome in item:
                return item[nome]
        else:
            valor = getattr(item, nome, None)
            if valor is not None:
                return valor
    return None


def _rotulo_mes(dia: Any) -> str:
    """``date(2025, 1, 31)`` -> ``'jan/2025'``; qualquer outra coisa vira texto."""
    if isinstance(dia, date):
        return f"{_MESES_PT[dia.month - 1]}/{dia.year:04d}"
    return _texto(dia)


def _iso(dia: Any) -> str:
    """Data em ISO (string) — mantém o JSON de saída sem objetos ``date``."""
    if isinstance(dia, date):
        return dia.isoformat()
    return _texto(dia)


def _decrescente(dados: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ordena por ``value`` do maior para o menor (estável em empates)."""
    return sorted(dados, key=lambda ponto: _numero(ponto.get("value")), reverse=True)


def _grafico(
    identificador: str,
    titulo: str,
    tipo: str,
    series: List[Dict[str, str]],
    dados: List[Dict[str, Any]],
    formato: str = "currency",
    nota: str = "",
) -> ChartSeries:
    return ChartSeries(
        id=identificador,
        title=titulo,
        kind=tipo,
        x_key="label",
        series=series,
        data=dados,
        value_format=formato,
        note=nota,
    )


# --------------------------------------------------------------------------
# Gráfico 1 — Distribuição por classe (donut)
# --------------------------------------------------------------------------


def _alocacao_classe(portfolio: Optional[PortfolioResult]) -> Optional[ChartSeries]:
    total = _numero(getattr(portfolio, "total_value", 0.0)) if portfolio else 0.0
    dados: List[Dict[str, Any]] = []
    for classe in _itens(portfolio, "by_class"):
        valor = _numero(_campo(classe, "value"))
        rotulo = _texto(_campo(classe, "asset_class", "label")) or "Outros"
        peso = _opcional(_campo(classe, "weight"))
        dados.append(
            {
                "label": rotulo,
                "value": valor,
                "share": peso if peso is not None else _divide(valor, total),
                "positions": int(_numero(_campo(classe, "positions"))),
            }
        )
    if not dados:
        return None
    return _grafico(
        "alocacao-classe",
        "Distribuição por classe",
        "donut",
        [{"key": "value", "label": "Valor (R$)"}],
        _decrescente(dados),
        nota="Participação de cada classe no valor de mercado da carteira B3.",
    )


# --------------------------------------------------------------------------
# Gráfico 2 — Top 10 posições (bar)
# --------------------------------------------------------------------------


def _top_posicoes(portfolio: Optional[PortfolioResult]) -> Optional[ChartSeries]:
    dados: List[Dict[str, Any]] = []
    for posicao in _itens(portfolio, "positions"):
        ticker = _texto(_campo(posicao, "ticker"))
        nome = _texto(_campo(posicao, "name"))
        dados.append(
            {
                "label": ticker or nome or "(sem ticker)",
                "value": _numero(_campo(posicao, "market_value")),
                "name": nome or ticker,
                "asset_class": _texto(_campo(posicao, "asset_class")) or "Outros",
                "share": _numero(_campo(posicao, "portfolio_weight")),
            }
        )
    if not dados:
        return None
    return _grafico(
        "top-posicoes",
        "Top 10 posições (R$)",
        "bar",
        [{"key": "value", "label": "Valor (R$)"}],
        _decrescente(dados)[:TOP_POSICOES],
        nota="Maiores posições por valor de mercado, do maior para o menor.",
    )


# --------------------------------------------------------------------------
# Gráfico 3 — Rentabilidade 12m: 5 melhores e 5 piores (bar, percentual)
# --------------------------------------------------------------------------


def _melhores_piores(portfolio: Optional[PortfolioResult]) -> Optional[ChartSeries]:
    candidatas: List[Tuple[float, Dict[str, Any]]] = []
    for posicao in _itens(portfolio, "positions"):
        variacao = _opcional(_campo(posicao, "var_12m"))
        if variacao is None:  # sem o dado não entra no ranking
            continue
        ticker = _texto(_campo(posicao, "ticker"))
        nome = _texto(_campo(posicao, "name"))
        candidatas.append(
            (
                variacao,
                {
                    "label": ticker or nome or "(sem ticker)",
                    "value": variacao,
                    "name": nome or ticker,
                    "asset_class": _texto(_campo(posicao, "asset_class")) or "Outros",
                    "market_value": _numero(_campo(posicao, "market_value")),
                },
            )
        )
    if not candidatas:
        return None

    # Estável: em empate vale a ordem da carteira (já ordenada por valor).
    ordenadas = sorted(candidatas, key=lambda par: par[0], reverse=True)
    quantidade = len(ordenadas)
    # Índices das duas pontas; o ``set`` evita repetir alguém quando há menos
    # de 10 ativos com var_12m, e o ``sorted`` mantém a ordem melhor -> pior.
    topo = set(range(min(EXTREMOS_VAR_12M, quantidade)))
    fundo = set(range(max(0, quantidade - EXTREMOS_VAR_12M), quantidade))
    dados: List[Dict[str, Any]] = []
    for indice in sorted(topo | fundo):
        ponto = dict(ordenadas[indice][1])
        ponto["group"] = "Melhores" if indice in topo else "Piores"
        dados.append(ponto)

    return _grafico(
        "melhores-piores",
        "Rentabilidade 12m — 5 melhores e 5 piores",
        "bar",
        [{"key": "value", "label": "Rentabilidade 12m"}],
        dados,
        formato="percent",
        nota="Ordenado do melhor para o pior; ativos sem variação 12m ficam de fora.",
    )


# --------------------------------------------------------------------------
# Gráfico 4 — Maiores proventos do período (bar)
# --------------------------------------------------------------------------


def _maiores_proventos(proventos: Optional[ProventosResult]) -> Optional[ChartSeries]:
    origem = _itens(proventos, "top") or _itens(proventos, "by_ticker")
    dados: List[Dict[str, Any]] = []
    for item in origem:
        rotulo = _texto(_campo(item, "label")) or _texto(_campo(item, "ticker"))
        dados.append(
            {
                "label": rotulo or "(sem ticker)",
                "value": _numero(_campo(item, "amount", "value")),
                "share": _numero(_campo(item, "share")),
                "count": int(_numero(_campo(item, "count"))),
            }
        )
    if not dados:
        return None
    return _grafico(
        "maiores-proventos",
        "Maiores proventos no período (R$)",
        "bar",
        [{"key": "value", "label": "Proventos (R$)"}],
        _decrescente(dados)[:TOP_PROVENTOS],
        nota="Soma recebida por ativo no período, do maior para o menor.",
    )


# --------------------------------------------------------------------------
# Gráfico 5 — Alocação do patrimônio (donut)
# --------------------------------------------------------------------------


def _alocacao_patrimonio(consolidated: Optional[ConsolidatedResult]) -> Optional[ChartSeries]:
    total = _numero(getattr(consolidated, "total", 0.0)) if consolidated else 0.0
    dados: List[Dict[str, Any]] = []
    for grupo in _itens(consolidated, "by_category"):
        valor = _numero(_campo(grupo, "value"))
        rotulo = _texto(_campo(grupo, "label", "category")) or "Outros"
        participacao = _opcional(_campo(grupo, "share"))
        dados.append(
            {
                "label": rotulo,
                "value": valor,
                "share": participacao if participacao is not None else _divide(valor, total),
                "accounts": int(_numero(_campo(grupo, "accounts"))),
            }
        )
    if not dados:
        return None
    return _grafico(
        "alocacao-patrimonio",
        "Alocação do Patrimônio",
        "donut",
        [{"key": "value", "label": "Valor (R$)"}],
        _decrescente(dados),
        nota="Patrimônio consolidado por categoria (inclui exterior já convertido em R$).",
    )


# --------------------------------------------------------------------------
# Gráfico 6 — Fluxo de caixa × aporte (bar, 12 meses)
# --------------------------------------------------------------------------


def _fluxo_vs_aporte(cashflow: Optional[CashflowResult]) -> Optional[ChartSeries]:
    meses = _itens(cashflow, "monthly")
    if not meses:
        return None
    # Série temporal: preserva a ordem cronológica e recorta os 12 últimos.
    recorte = meses[-MESES_FLUXO:]
    dados: List[Dict[str, Any]] = []
    for mes in recorte:
        referencia = _campo(mes, "month")
        dados.append(
            {
                "label": _rotulo_mes(referencia),
                "month": _iso(referencia),
                "inflow": _numero(_campo(mes, "inflow")),
                "expenses": _numero(_campo(mes, "expenses")),
                "invest_contribution": _numero(_campo(mes, "invest_contribution")),
                "savings": _numero(_campo(mes, "savings")),
            }
        )
    return _grafico(
        "fluxo-vs-aporte",
        "Fluxo de Caixa × Aporte em Investimentos (12m)",
        "bar",
        [
            {"key": "inflow", "label": "Entradas"},
            {"key": "expenses", "label": "Gastos"},
            {"key": "invest_contribution", "label": "Aporte em investimentos"},
        ],
        dados,
        nota="Últimos 12 meses em ordem cronológica.",
    )


# --------------------------------------------------------------------------
# Gráfico 7 — Liquidez e reserva de oportunidade (bar)
# --------------------------------------------------------------------------


def _liquidez(consolidated: Optional[ConsolidatedResult]) -> Optional[ChartSeries]:
    total = _numero(getattr(consolidated, "total", 0.0)) if consolidated else 0.0
    dados: List[Dict[str, Any]] = []
    for grupo in _itens(consolidated, "by_liquidity"):
        valor = _numero(_campo(grupo, "value"))
        rotulo = _texto(_campo(grupo, "label", "liquidity")) or "Não informada"
        participacao = _opcional(_campo(grupo, "share"))
        dados.append(
            {
                "label": rotulo,
                "value": valor,
                "share": participacao if participacao is not None else _divide(valor, total),
                "accounts": int(_numero(_campo(grupo, "accounts"))),
            }
        )
    if not dados:
        return None
    return _grafico(
        "liquidez",
        "Liquidez e Reserva de Oportunidade",
        "bar",
        [{"key": "value", "label": "Valor (R$)"}],
        _decrescente(dados),
        nota="Quanto do patrimônio está disponível em cada prazo de resgate.",
    )


# --------------------------------------------------------------------------
# Gráfico 8 — Evolução do patrimônio no simulador (line)
# --------------------------------------------------------------------------


def _evolucao_simulador(simulation: Optional[SimulationResult]) -> Optional[ChartSeries]:
    pontos = _itens(simulation, "series")
    if not pontos:
        return None
    dados: List[Dict[str, Any]] = []
    for ponto in pontos:
        mes = int(_numero(_campo(ponto, "month")))
        dados.append(
            {
                "label": f"Mês {mes}",
                "month": mes,
                "balance": _numero(_campo(ponto, "balance")),
                "invested": _numero(_campo(ponto, "invested")),
                "interest": _numero(_campo(ponto, "interest")),
            }
        )
    return _grafico(
        "evolucao-simulador",
        "Evolução do patrimônio — Simulador",
        "line",
        [
            {"key": "balance", "label": "Saldo acumulado"},
            {"key": "invested", "label": "Total investido"},
            {"key": "interest", "label": "Juros"},
        ],
        dados,
        nota="Juros compostos sobre o capital inicial mais os aportes mensais.",
    )


# --------------------------------------------------------------------------
# Gráfico 9 — Projeção até 2030 (area)
# --------------------------------------------------------------------------


def _projecao_2030(projection: Optional[ProjectionResult]) -> Optional[ChartSeries]:
    pontos = _itens(projection, "series")
    if not pontos:
        return None
    dados: List[Dict[str, Any]] = []
    for ponto in pontos:
        referencia = _campo(ponto, "month")
        dados.append(
            {
                "label": _rotulo_mes(referencia),
                "month": _iso(referencia),
                "value_income": _numero(_campo(ponto, "value_income")),
                "value_growth": _numero(_campo(ponto, "value_growth")),
                "value_selected": _numero(_campo(ponto, "value_selected")),
                "contribution": _numero(_campo(ponto, "contribution")),
            }
        )
    cenario = _texto(getattr(projection, "scenario", "")) or "Valorizacao"
    return _grafico(
        "projecao-2030",
        "Projeção do patrimônio até 2030",
        "area",
        [
            {"key": "value_income", "label": "Cenário Renda"},
            {"key": "value_growth", "label": "Cenário Valorização"},
        ],
        dados,
        nota=f"Cenário selecionado no Config: {cenario}.",
    )


# --------------------------------------------------------------------------
# Entrada pública
# --------------------------------------------------------------------------


def build_charts(
    portfolio: Optional[PortfolioResult] = None,
    proventos: Optional[ProventosResult] = None,
    rebalance: Optional[RebalanceResult] = None,
    simulation: Optional[SimulationResult] = None,
    projection: Optional[ProjectionResult] = None,
    scenarios: Optional[ScenarioResult] = None,
    cashflow: Optional[CashflowResult] = None,
    consolidated: Optional[ConsolidatedResult] = None,
) -> List[ChartSeries]:
    """Monta as séries dos 9 gráficos a partir dos resultados disponíveis.

    Todo argumento é opcional: cada gráfico só é gerado quando a sua origem
    existe e tem dados. Uma planilha só com a aba ``Posicao_B3``, por exemplo,
    produz ``alocacao-classe``, ``top-posicoes`` e ``melhores-piores`` — sem
    erro e sem gráficos vazios.

    ``rebalance`` e ``scenarios`` fazem parte do contrato (o pipeline passa
    todos os resultados), mas hoje não alimentam nenhum dos 9 gráficos: o
    rebalanceamento vira tabela e os cenários viram KPIs no front-end.
    """
    candidatos: Iterable[Optional[ChartSeries]] = (
        _alocacao_classe(portfolio),
        _top_posicoes(portfolio),
        _melhores_piores(portfolio),
        _maiores_proventos(proventos),
        _alocacao_patrimonio(consolidated),
        _fluxo_vs_aporte(cashflow),
        _liquidez(consolidated),
        _evolucao_simulador(simulation),
        _projecao_2030(projection),
    )
    return [grafico for grafico in candidatos if grafico is not None]


def chart_by_id(charts: Sequence[ChartSeries], identificador: str) -> Optional[ChartSeries]:
    """Atalho de busca por id (usado pelo pipeline e pelos testes)."""
    for grafico in charts or ():
        if grafico is not None and getattr(grafico, "id", "") == identificador:
            return grafico
    return None
