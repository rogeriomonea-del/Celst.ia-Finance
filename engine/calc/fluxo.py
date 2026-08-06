"""Fluxo de caixa e saldo de investimentos projetado.

Substitui três abas da planilha de uma vez:

``Saldo_Invest_Diario``
    a projeção diária do saldo investido. A recursão é a mesma da coluna E:
    ``saldo_0 = capital_inicial*(1+d) + aporte_0 - retirada_0`` e
    ``saldo_t = saldo_{t-1}*(1+d) + aporte_t - retirada_t``, com a taxa diária
    efetiva ``d = (1+retorno_anual)^(1/365) - 1``. Os aportes vêm da coluna
    "Aporte Investimentos" do fluxo de caixa; a retirada é a
    ``retirada mensal planejada`` do Config e só entra **no dia 1 do mês**, a
    partir de ``start_date`` (o ``IF(AND($A>=Config!$B$17;DAY($A)=1);…)``).

``Fluxo de Caixa``
    as colunas acumuladas (saldo de caixa, patrimônio = investido + caixa).
    A planilha deixou a coluna "Cash" vazia; aqui o caixa é **recalculado**
    como o acumulado de ``entradas − gastos totais − aporte investimentos −
    aporte reserva``.

``FluxoCaixa_Mensal``
    o resumo mês a mês (os ``SUMIFS`` por mês mais o ``INDEX/MATCH`` que
    buscava o saldo investido do último dia do mês).

Módulo **puro**: entram dataclasses de ``engine.models``, saem dataclasses de
``engine.models``. Sem I/O, sem rede, sem openpyxl.

Formato de ``CashflowResult.daily_balance`` (um dicionário por ponto):

``day``           data do dia (``date``)
``contribution``  aporte em investimentos do dia (R$)
``withdrawal``    retirada planejada do dia (R$)
``balance``       saldo investido projetado no fim do dia (R$)

**Amostragem**: são ~2.200 dias no arquivo real e o JSON vai inteiro para o
navegador, então ``daily_balance`` devolve no máximo
:data:`LIMITE_PONTOS_DIARIOS` pontos, escolhidos por índice uniformemente
espaçado (``round(i * (n-1) / (k-1))``), sempre incluindo o **primeiro** e o
**último** dia. A curva fica visualmente idêntica, mas os pontos intermediários
são amostras: quem precisa de somas exatas usa ``monthly`` e os totais, que são
calculados sobre **todos** os dias.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from ..models import CashflowEntry, CashflowResult, MonthlyCashflow, WorkbookInputs

#: Teto de pontos devolvidos em ``daily_balance`` (ver amostragem no topo).
LIMITE_PONTOS_DIARIOS: int = 800

#: Dias usados na conversão da taxa anual para diária, como na planilha.
DIAS_POR_ANO: int = 365


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _numero(valor: Any) -> float:
    """Converte para ``float`` tratando ``None``, texto inválido, NaN e infinito."""
    # Booleano é lixo de planilha numa coluna de dinheiro: ``True`` virar 1,00
    # inventaria patrimônio, então tratamos como ausência de valor.
    if valor is None or isinstance(valor, bool):
        return 0.0
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return 0.0
    # NaN não é igual a si mesmo; infinito envenenaria todas as somas seguintes.
    if numero != numero or numero in (float("inf"), float("-inf")):
        return 0.0
    return numero


def _como_data(valor: Any) -> Optional[date]:
    """Aceita ``date`` e ``datetime``; qualquer outra coisa vira ``None``."""
    # ``datetime`` é subclasse de ``date``: testar primeiro evita guardar o
    # horário e quebrar a comparação com as datas puras do Config.
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


def _divide(numerador: float, denominador: float) -> float:
    """Divisão que devolve 0.0 em vez de estourar quando o denominador é zero."""
    if not denominador:
        return 0.0
    try:
        resultado = numerador / denominador
    except (ZeroDivisionError, OverflowError):
        return 0.0
    return _numero(resultado)


def taxa_diaria(taxa_anual: float) -> float:
    """Taxa diária efetiva equivalente à anual: ``(1+a)^(1/365) - 1``.

    Uma taxa anual de -100% ou pior (o capital some) vira -100% ao dia, em vez
    de virar raiz de número negativo.
    """
    anual = _numero(taxa_anual)
    if anual == 0.0:
        return 0.0
    base = 1.0 + anual
    if base <= 0.0:
        return -1.0
    try:
        return base ** (1.0 / DIAS_POR_ANO) - 1.0
    except (OverflowError, ValueError):  # pragma: no cover - base já é > 0
        return 0.0


def _primeiro_do_mes(dia: date) -> date:
    """Chave de agrupamento mensal: o dia 1 do mês, como na coluna A do resumo."""
    return date(dia.year, dia.month, 1)


# --------------------------------------------------------------------------
# Preparo das linhas diárias
# --------------------------------------------------------------------------


def _linhas_por_dia(entradas: Any) -> List[Tuple[date, float, float, float, float]]:
    """Consolida o fluxo em ``(dia, entradas, gastos, aporte, reserva)`` ordenado.

    Linhas sem data são descartadas (não têm onde entrar na recursão diária) e
    linhas repetidas do mesmo dia são somadas — um dia rende juros uma vez só,
    então duplicatas não podem virar dois passos de capitalização.
    """
    if not entradas:
        return []
    acumulado: Dict[date, List[float]] = {}
    for linha in entradas:
        if linha is None:
            continue
        dia = _como_data(getattr(linha, "day", None))
        if dia is None:
            continue
        alvo = acumulado.get(dia)
        if alvo is None:
            alvo = [0.0, 0.0, 0.0, 0.0]
            acumulado[dia] = alvo
        alvo[0] += _numero(getattr(linha, "inflow", 0.0))
        # ``total_expenses`` = fatura do cartão + gastos (coluna "Gastos Totais").
        alvo[1] += _numero(getattr(linha, "card_bill", 0.0)) + _numero(
            getattr(linha, "expenses", 0.0)
        )
        alvo[2] += _numero(getattr(linha, "invest_contribution", 0.0))
        alvo[3] += _numero(getattr(linha, "reserve_contribution", 0.0))
    return [(dia, *acumulado[dia]) for dia in sorted(acumulado)]


def _retirada_do_dia(
    dia: date, retirada_mensal: float, inicio: Optional[date]
) -> float:
    """Retirada planejada: só no dia 1 do mês e só a partir de ``start_date``.

    Sem ``start_date`` a planilha compara com uma célula vazia (= 0), o que
    deixa **toda** data elegível; mantemos esse comportamento.
    """
    if not retirada_mensal or dia.day != 1:
        return 0.0
    if inicio is not None and dia < inicio:
        return 0.0
    return retirada_mensal


def _amostra(pontos: List[Dict[str, Any]], limite: int = LIMITE_PONTOS_DIARIOS) -> List[Dict[str, Any]]:
    """Reduz a série diária a no máximo ``limite`` pontos igualmente espaçados.

    O primeiro e o último dia entram sempre — são eles que ancoram o começo e o
    fim da curva no gráfico. A exceção é ``limite == 1``, que não comporta os
    dois: aí fica só o último ponto, o saldo final.
    """
    total = len(pontos)
    if limite <= 0 or total == 0:
        return []
    if total <= limite:
        return list(pontos)
    if limite == 1:
        return [pontos[-1]]
    passo = (total - 1) / (limite - 1)
    indices = sorted({min(total - 1, int(round(i * passo))) for i in range(limite)})
    return [pontos[i] for i in indices]


# --------------------------------------------------------------------------
# Entrada pública
# --------------------------------------------------------------------------


def compute_cashflow(inputs: WorkbookInputs, annual_rate: float) -> CashflowResult:
    """Projeta o saldo investido dia a dia e resume o fluxo de caixa por mês.

    ``annual_rate`` é o retorno esperado da carteira (Config B20 na planilha).
    Um único passe sobre os ~2.200 dias produz, ao mesmo tempo, a série diária,
    os baldes mensais e os totais do período. Sem linhas de fluxo de caixa
    devolve um resultado zerado — nunca exceção.
    """
    linhas = _linhas_por_dia(getattr(inputs, "cashflow", None))
    assumptions = getattr(inputs, "assumptions", None)
    capital = _numero(getattr(assumptions, "initial_capital", 0.0))
    retirada_mensal = _numero(getattr(assumptions, "monthly_withdrawal", 0.0))
    inicio = _como_data(getattr(assumptions, "start_date", None))
    diaria = taxa_diaria(annual_rate)

    if not linhas:
        return CashflowResult(
            monthly=[],
            daily_balance=[],
            total_inflow=0.0,
            total_expenses=0.0,
            total_contributions=0.0,
            savings_rate=0.0,
            final_invested=0.0,
        )

    diarios: List[Dict[str, Any]] = []
    # {mês: [entradas, gastos, aporte, investido no fim do mês, caixa no fim]}
    meses: Dict[date, List[float]] = {}
    ordem_meses: List[date] = []

    saldo = capital
    caixa = 0.0
    total_entradas = 0.0
    total_gastos = 0.0
    total_aportes = 0.0

    for dia, entrada, gastos, aporte, reserva in linhas:
        retirada = _retirada_do_dia(dia, retirada_mensal, inicio)
        # Mesma ordem de operações da coluna E: rende, recebe o aporte, paga a
        # retirada.
        saldo = saldo * (1.0 + diaria) + aporte - retirada
        # O caixa é o acumulado do que sobra depois dos aportes do dia.
        caixa += entrada - gastos - aporte - reserva

        total_entradas += entrada
        total_gastos += gastos
        total_aportes += aporte

        diarios.append(
            {
                "day": dia,
                "contribution": aporte,
                "withdrawal": retirada,
                "balance": saldo,
            }
        )

        chave = _primeiro_do_mes(dia)
        balde = meses.get(chave)
        if balde is None:
            balde = [0.0, 0.0, 0.0, 0.0, 0.0]
            meses[chave] = balde
            ordem_meses.append(chave)
        balde[0] += entrada
        balde[1] += gastos
        balde[2] += aporte
        # Como os dias vêm ordenados, sobrescrever deixa no balde o saldo do
        # último dia do mês — o mesmo que o INDEX/MATCH com EOMONTH buscava.
        balde[3] = saldo
        balde[4] = caixa

    mensal: List[MonthlyCashflow] = [
        MonthlyCashflow(
            month=chave,
            inflow=meses[chave][0],
            expenses=meses[chave][1],
            invest_contribution=meses[chave][2],
            savings=meses[chave][0] - meses[chave][1],
            invested_balance=meses[chave][3],
            cash_balance=meses[chave][4],
            net_worth=meses[chave][3] + meses[chave][4],
        )
        for chave in ordem_meses
    ]

    return CashflowResult(
        monthly=mensal,
        daily_balance=_amostra(diarios),
        total_inflow=total_entradas,
        total_expenses=total_gastos,
        # Só o aporte em investimentos, como a coluna "Aporte Investimentos"
        # do resumo mensal (o aporte para a reserva sai do caixa mas não entra
        # na projeção de investimentos).
        total_contributions=total_aportes,
        savings_rate=_divide(total_entradas - total_gastos, total_entradas),
        final_invested=diarios[-1]["balance"],
    )
