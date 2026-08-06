"""Testes de ``engine.calc.fluxo``.

Fixtures **sintéticas**: as linhas diárias abaixo são construídas à mão com os
mesmos parâmetros do Config (capital inicial 2.440.000, retirada 0, taxa
esperada da carteira), então os números conferidos contra a planilha original
são reproduzidos sem tocar em nenhum arquivo real.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc.fluxo import (  # noqa: E402
    LIMITE_PONTOS_DIARIOS,
    compute_cashflow,
    taxa_diaria,
)
from engine.models import (  # noqa: E402
    Assumptions,
    CashflowEntry,
    WorkbookInputs,
    to_dict,
)

REL = 1e-9

# Retorno esperado da carteira (Config B20) — a taxa que alimenta a projeção
# diária do saldo investido.
TAXA_ESPERADA = 0.11207114672179158

# Taxa diária efetiva correspondente (coluna D de Saldo_Invest_Diario).
TAXA_DIARIA_ESPERADA = 0.0002910674879414987

CAPITAL_INICIAL = 2_440_000.0


# --------------------------------------------------------------------------
# Fixtures sintéticas
# --------------------------------------------------------------------------


def _config(
    capital: float = CAPITAL_INICIAL,
    retirada: float = 0.0,
    inicio: Optional[date] = None,
) -> Assumptions:
    """Config da planilha: capital inicial, retirada planejada e data inicial."""
    return Assumptions(
        initial_capital=capital,
        monthly_contribution=120_000.0,
        monthly_withdrawal=retirada,
        start_date=inicio,
    )


def _dias(inicio: date, fim: date) -> List[date]:
    """Todos os dias do intervalo, inclusive as duas pontas."""
    total = (fim - inicio).days + 1
    return [inicio + timedelta(days=i) for i in range(total)]


def _fluxo_jan_mai() -> WorkbookInputs:
    """2025-01-01 a 2025-05-31, com as entradas mensais do arquivo real.

    Nesses cinco meses a planilha não tem aporte em investimentos nem gastos,
    então o saldo investido é só capitalização — é o trecho que fecha a
    paridade dia a dia e mês a mês.
    """
    entradas = {
        date(2025, 1, 10): 7_000.0,
        date(2025, 2, 10): 7_000.0,
        date(2025, 3, 10): 7_000.0,
        date(2025, 4, 15): 18_300.0,
        date(2025, 5, 20): 22_956.0,
    }
    linhas = [
        CashflowEntry(day=dia, inflow=entradas.get(dia, 0.0))
        for dia in _dias(date(2025, 1, 1), date(2025, 5, 31))
    ]
    return WorkbookInputs(cashflow=linhas, assumptions=_config())


# --------------------------------------------------------------------------
# Taxa diária
# --------------------------------------------------------------------------


def test_taxa_diaria_reproduz_a_coluna_da_planilha() -> None:
    assert taxa_diaria(TAXA_ESPERADA) == pytest.approx(TAXA_DIARIA_ESPERADA, rel=REL)


def test_taxa_diaria_degenerada_nao_levanta() -> None:
    assert taxa_diaria(0.0) == 0.0
    assert taxa_diaria(None) == 0.0  # type: ignore[arg-type]
    assert taxa_diaria("abacaxi") == 0.0  # type: ignore[arg-type]
    assert taxa_diaria(float("nan")) == 0.0
    # -100% ou pior: o capital some, sem raiz de número negativo.
    assert taxa_diaria(-1.0) == -1.0
    assert taxa_diaria(-2.5) == -1.0


# --------------------------------------------------------------------------
# Paridade com a planilha
# --------------------------------------------------------------------------


def test_primeiros_tres_dias_batem_com_saldo_invest_diario() -> None:
    resultado = compute_cashflow(_fluxo_jan_mai(), TAXA_ESPERADA)
    saldos = [ponto["balance"] for ponto in resultado.daily_balance[:3]]
    assert saldos[0] == pytest.approx(2_440_710.2046705773, rel=REL)
    assert saldos[1] == pytest.approx(2_441_420.616058644, rel=REL)
    assert saldos[2] == pytest.approx(2_442_131.234224369, rel=REL)


def test_resumo_mensal_bate_com_fluxocaixa_mensal() -> None:
    resultado = compute_cashflow(_fluxo_jan_mai(), TAXA_ESPERADA)
    por_mes = {linha.month: linha for linha in resultado.monthly}
    assert list(por_mes) == [date(2025, mes, 1) for mes in range(1, 6)]

    janeiro = por_mes[date(2025, 1, 1)]
    assert janeiro.inflow == pytest.approx(7_000.0, rel=REL)
    assert janeiro.expenses == 0.0
    assert janeiro.invest_contribution == 0.0
    assert janeiro.savings == pytest.approx(7_000.0, rel=REL)
    assert janeiro.invested_balance == pytest.approx(2_462_112.739430867, rel=REL)

    fevereiro = por_mes[date(2025, 2, 1)]
    assert fevereiro.invested_balance == pytest.approx(2_482_257.7332102666, rel=REL)

    assert por_mes[date(2025, 4, 1)].inflow == pytest.approx(18_300.0, rel=REL)
    assert por_mes[date(2025, 5, 1)].inflow == pytest.approx(22_956.0, rel=REL)


def test_totais_do_periodo_e_saldo_final() -> None:
    resultado = compute_cashflow(_fluxo_jan_mai(), TAXA_ESPERADA)
    assert resultado.total_inflow == pytest.approx(62_256.0, rel=REL)
    assert resultado.total_expenses == 0.0
    assert resultado.total_contributions == 0.0
    # Sem gastos, toda entrada vira poupança.
    assert resultado.savings_rate == pytest.approx(1.0, rel=REL)
    # final_invested é o último saldo projetado, e o mês final espelha esse valor.
    assert resultado.final_invested == resultado.daily_balance[-1]["balance"]
    assert resultado.final_invested == pytest.approx(
        resultado.monthly[-1].invested_balance, rel=REL
    )


def test_saldo_investido_do_mes_e_o_do_ultimo_dia() -> None:
    resultado = compute_cashflow(_fluxo_jan_mai(), TAXA_ESPERADA)
    # 151 dias < LIMITE_PONTOS_DIARIOS: nenhuma amostragem, dá para casar
    # o ponto do dia 31/01 com o fechamento de janeiro.
    saldos = {ponto["day"]: ponto["balance"] for ponto in resultado.daily_balance}
    assert saldos[date(2025, 1, 31)] == pytest.approx(
        resultado.monthly[0].invested_balance, rel=REL
    )
    assert saldos[date(2025, 2, 28)] == pytest.approx(
        resultado.monthly[1].invested_balance, rel=REL
    )


# --------------------------------------------------------------------------
# Recursão: aportes, retiradas e caixa
# --------------------------------------------------------------------------


def test_recursao_do_primeiro_dia_usa_o_capital_inicial() -> None:
    """``saldo_0 = capital*(1+d) + aporte_0 - retirada_0``."""
    linhas = [CashflowEntry(day=date(2025, 3, 5), invest_contribution=1_000.0)]
    inputs = WorkbookInputs(cashflow=linhas, assumptions=_config())
    resultado = compute_cashflow(inputs, TAXA_ESPERADA)
    esperado = CAPITAL_INICIAL * (1 + TAXA_DIARIA_ESPERADA) + 1_000.0
    assert resultado.daily_balance[0]["balance"] == pytest.approx(esperado, rel=REL)
    assert resultado.daily_balance[0]["contribution"] == 1_000.0
    assert resultado.daily_balance[0]["withdrawal"] == 0.0


def test_taxa_zero_soma_aportes_sem_juros() -> None:
    linhas = [
        CashflowEntry(day=date(2025, 1, 1), invest_contribution=100.0),
        CashflowEntry(day=date(2025, 1, 2), invest_contribution=250.0),
        CashflowEntry(day=date(2025, 1, 3)),
    ]
    inputs = WorkbookInputs(cashflow=linhas, assumptions=_config(capital=1_000.0))
    resultado = compute_cashflow(inputs, 0.0)
    assert [p["balance"] for p in resultado.daily_balance] == [1_100.0, 1_350.0, 1_350.0]
    assert resultado.total_contributions == pytest.approx(350.0, rel=REL)


def test_retirada_so_no_dia_1_e_a_partir_da_data_inicial() -> None:
    """``IF(AND(data >= start_date; DAY(data)=1); retirada; 0)``."""
    linhas = [
        CashflowEntry(day=dia)
        for dia in _dias(date(2025, 1, 1), date(2025, 4, 2))
    ]
    inputs = WorkbookInputs(
        cashflow=linhas,
        assumptions=_config(capital=100_000.0, retirada=5_000.0, inicio=date(2025, 3, 15)),
    )
    resultado = compute_cashflow(inputs, 0.0)
    retiradas = {p["day"]: p["withdrawal"] for p in resultado.daily_balance}
    # Antes da data inicial: nada, mesmo no dia 1.
    assert retiradas[date(2025, 1, 1)] == 0.0
    assert retiradas[date(2025, 3, 1)] == 0.0
    # Depois da data inicial: só no dia 1.
    assert retiradas[date(2025, 4, 1)] == 5_000.0
    assert retiradas[date(2025, 4, 2)] == 0.0
    # Sem juros, o saldo final é capital - uma única retirada.
    assert resultado.final_invested == pytest.approx(95_000.0, rel=REL)


def test_sem_data_inicial_a_retirada_vale_para_todo_dia_1() -> None:
    """Config com data inicial vazia: a planilha compara com 0 e libera tudo."""
    linhas = [CashflowEntry(day=dia) for dia in _dias(date(2025, 1, 1), date(2025, 3, 1))]
    inputs = WorkbookInputs(
        cashflow=linhas, assumptions=_config(capital=100_000.0, retirada=1_000.0)
    )
    resultado = compute_cashflow(inputs, 0.0)
    assert resultado.final_invested == pytest.approx(97_000.0, rel=REL)


def test_caixa_acumulado_e_patrimonio() -> None:
    """Caixa = acumulado de entradas − gastos − aporte investimentos − reserva."""
    linhas = [
        CashflowEntry(
            day=date(2025, 1, 10),
            inflow=10_000.0,
            card_bill=1_500.0,
            expenses=500.0,
            invest_contribution=3_000.0,
            reserve_contribution=1_000.0,
        ),
        CashflowEntry(day=date(2025, 1, 31)),
        CashflowEntry(day=date(2025, 2, 10), inflow=2_000.0, expenses=200.0),
        CashflowEntry(day=date(2025, 2, 28)),
    ]
    inputs = WorkbookInputs(cashflow=linhas, assumptions=_config(capital=0.0))
    resultado = compute_cashflow(inputs, 0.0)

    janeiro, fevereiro = resultado.monthly
    # Gastos totais = fatura do cartão + gastos.
    assert janeiro.expenses == pytest.approx(2_000.0, rel=REL)
    assert janeiro.savings == pytest.approx(8_000.0, rel=REL)
    assert janeiro.cash_balance == pytest.approx(4_000.0, rel=REL)
    assert janeiro.invested_balance == pytest.approx(3_000.0, rel=REL)
    assert janeiro.net_worth == pytest.approx(7_000.0, rel=REL)
    # Fevereiro acumula em cima de janeiro.
    assert fevereiro.cash_balance == pytest.approx(5_800.0, rel=REL)
    assert fevereiro.net_worth == pytest.approx(8_800.0, rel=REL)

    assert resultado.total_inflow == pytest.approx(12_000.0, rel=REL)
    assert resultado.total_expenses == pytest.approx(2_200.0, rel=REL)
    assert resultado.savings_rate == pytest.approx(9_800.0 / 12_000.0, rel=REL)


def test_dias_repetidos_somam_e_capitalizam_uma_vez_so() -> None:
    linhas = [
        CashflowEntry(day=date(2025, 1, 1), inflow=100.0, invest_contribution=10.0),
        CashflowEntry(day=date(2025, 1, 1), inflow=50.0, invest_contribution=5.0),
    ]
    inputs = WorkbookInputs(cashflow=linhas, assumptions=_config(capital=1_000.0))
    resultado = compute_cashflow(inputs, 0.0)
    assert len(resultado.daily_balance) == 1
    assert resultado.daily_balance[0]["contribution"] == pytest.approx(15.0, rel=REL)
    assert resultado.final_invested == pytest.approx(1_015.0, rel=REL)
    assert resultado.total_inflow == pytest.approx(150.0, rel=REL)


def test_linhas_fora_de_ordem_sao_ordenadas() -> None:
    linhas = [
        CashflowEntry(day=date(2025, 2, 1), invest_contribution=200.0),
        CashflowEntry(day=date(2025, 1, 1), invest_contribution=100.0),
    ]
    inputs = WorkbookInputs(cashflow=linhas, assumptions=_config(capital=0.0))
    resultado = compute_cashflow(inputs, 0.0)
    assert [p["day"] for p in resultado.daily_balance] == [
        date(2025, 1, 1),
        date(2025, 2, 1),
    ]
    assert [linha.month for linha in resultado.monthly] == [
        date(2025, 1, 1),
        date(2025, 2, 1),
    ]


# --------------------------------------------------------------------------
# Amostragem da série diária
# --------------------------------------------------------------------------


def test_serie_diaria_e_amostrada_mantendo_as_pontas() -> None:
    dias = _dias(date(2025, 1, 1), date(2030, 12, 31))  # 2.191 dias, como o arquivo
    linhas = [CashflowEntry(day=dia, inflow=10.0) for dia in dias]
    inputs = WorkbookInputs(cashflow=linhas, assumptions=_config())
    resultado = compute_cashflow(inputs, TAXA_ESPERADA)

    assert len(dias) > LIMITE_PONTOS_DIARIOS
    assert len(resultado.daily_balance) == LIMITE_PONTOS_DIARIOS
    assert resultado.daily_balance[0]["day"] == dias[0]
    assert resultado.daily_balance[-1]["day"] == dias[-1]
    # Ordem cronológica estrita, sem repetição.
    amostrados = [p["day"] for p in resultado.daily_balance]
    assert amostrados == sorted(set(amostrados))
    # Espaçamento uniforme: nenhum buraco maior que o passo teórico + 1 dia.
    passo = (len(dias) - 1) / (LIMITE_PONTOS_DIARIOS - 1)
    maior = max(
        (b - a).days for a, b in zip(amostrados, amostrados[1:])
    )
    assert maior <= int(passo) + 1
    # Os totais e o mensal continuam somando TODOS os dias, não a amostra.
    assert resultado.total_inflow == pytest.approx(10.0 * len(dias), rel=REL)
    assert len(resultado.monthly) == 72
    assert resultado.final_invested == resultado.daily_balance[-1]["balance"]


def test_serie_curta_nao_e_amostrada() -> None:
    dias = _dias(date(2025, 1, 1), date(2025, 3, 31))
    inputs = WorkbookInputs(
        cashflow=[CashflowEntry(day=dia) for dia in dias], assumptions=_config()
    )
    resultado = compute_cashflow(inputs, TAXA_ESPERADA)
    assert len(resultado.daily_balance) == len(dias)


# --------------------------------------------------------------------------
# Casos degenerados — nunca levantam exceção
# --------------------------------------------------------------------------


def test_sem_fluxo_de_caixa_devolve_zeros() -> None:
    resultado = compute_cashflow(WorkbookInputs(), TAXA_ESPERADA)
    assert resultado.monthly == []
    assert resultado.daily_balance == []
    assert resultado.total_inflow == 0.0
    assert resultado.total_expenses == 0.0
    assert resultado.total_contributions == 0.0
    assert resultado.savings_rate == 0.0
    assert resultado.final_invested == 0.0


def test_entradas_nulas_ou_sem_data_sao_ignoradas() -> None:
    linhas = [
        None,
        CashflowEntry(day=None, inflow=999.0),  # type: ignore[arg-type]
        CashflowEntry(day=date(2025, 1, 1), inflow=100.0),
    ]
    inputs = WorkbookInputs(cashflow=linhas, assumptions=_config(capital=0.0))  # type: ignore[arg-type]
    resultado = compute_cashflow(inputs, TAXA_ESPERADA)
    assert len(resultado.daily_balance) == 1
    assert resultado.total_inflow == pytest.approx(100.0, rel=REL)


def test_valores_invalidos_viram_zero() -> None:
    linhas = [
        CashflowEntry(
            day=date(2025, 1, 1),
            inflow=None,  # type: ignore[arg-type]
            expenses="abacaxi",  # type: ignore[arg-type]
            invest_contribution=float("nan"),
            card_bill=float("inf"),
            reserve_contribution=True,  # type: ignore[arg-type]
        )
    ]
    inputs = WorkbookInputs(cashflow=linhas, assumptions=_config(capital=1_000.0))
    resultado = compute_cashflow(inputs, 0.0)
    assert resultado.final_invested == pytest.approx(1_000.0, rel=REL)
    assert resultado.total_inflow == 0.0
    assert resultado.total_expenses == 0.0
    assert resultado.monthly[0].cash_balance == 0.0


def test_sem_entradas_a_taxa_de_poupanca_e_zero() -> None:
    linhas = [CashflowEntry(day=date(2025, 1, 1), expenses=500.0)]
    inputs = WorkbookInputs(cashflow=linhas, assumptions=_config(capital=0.0))
    resultado = compute_cashflow(inputs, TAXA_ESPERADA)
    assert resultado.total_inflow == 0.0
    assert resultado.savings_rate == 0.0
    assert resultado.monthly[0].savings == pytest.approx(-500.0, rel=REL)


def test_config_ausente_e_taxa_invalida_nao_levantam() -> None:
    linhas = [CashflowEntry(day=date(2025, 1, 1), inflow=10.0)]
    inputs = WorkbookInputs(cashflow=linhas)
    inputs.assumptions = None  # type: ignore[assignment]
    resultado = compute_cashflow(inputs, None)  # type: ignore[arg-type]
    assert resultado.final_invested == 0.0
    assert resultado.total_inflow == pytest.approx(10.0, rel=REL)

    # Taxa de -100%: o saldo investido é zerado, sem raiz de número negativo.
    inputs2 = WorkbookInputs(cashflow=linhas, assumptions=_config(capital=1_000.0))
    assert compute_cashflow(inputs2, -1.0).final_invested == 0.0


def test_resultado_e_serializavel_em_json() -> None:
    import json

    resultado = compute_cashflow(_fluxo_jan_mai(), TAXA_ESPERADA)
    bruto = json.dumps(to_dict(resultado))
    assert '"day": "2025-01-01"' in bruto
    assert '"month": "2025-01-01"' in bruto
