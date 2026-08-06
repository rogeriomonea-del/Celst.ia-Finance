"""Testes de ``engine.calc.simulador`` e ``engine.calc.projecao``.

Fixtures **sintéticas**: os parâmetros abaixo são os mesmos que o
``Assumptions`` já traz por padrão, então os números conferidos contra a
planilha original são reproduzidos sem tocar em nenhum arquivo real.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc.projecao import annual_returns, compute_projection  # noqa: E402
from engine.calc.simulador import compute_simulation, taxa_mensal  # noqa: E402
from engine.models import Assumptions  # noqa: E402

REL = 1e-9

# Retorno esperado da carteira nos cenários — é a taxa que a planilha leva
# para o simulador (Config B20).
TAXA_ESPERADA = 0.11207114672179158


# --------------------------------------------------------------------------
# Fixtures sintéticas
# --------------------------------------------------------------------------


def _config_simulador() -> Assumptions:
    """Entradas da aba Simulador: 2.400.000 iniciais + 120.000/mês, 4 anos."""
    return Assumptions(initial_capital=2_400_000.0, monthly_contribution=120_000.0)


def _config_projecao() -> Assumptions:
    """Entradas da aba Config usadas pela Projecao_2030."""
    return Assumptions(
        scenario="Valorizacao",
        initial_capital=2_440_000.0,
        monthly_contribution=120_000.0,
        monthly_withdrawal=0.0,
        monthly_income_target=18_707.34,
        start_date=date(2026, 3, 1),
        end_date=date(2030, 12, 31),
    )


# --------------------------------------------------------------------------
# Simulador — paridade com a planilha
# --------------------------------------------------------------------------


def test_simulador_reproduz_o_painel_da_planilha() -> None:
    resultado = compute_simulation(_config_simulador(), TAXA_ESPERADA)

    assert resultado.months == 48
    assert resultado.monthly_rate == pytest.approx(0.008891309495601352, rel=REL)
    assert resultado.final_balance == pytest.approx(10_816_029.64, rel=REL)
    assert resultado.total_invested == pytest.approx(8_160_000.0, rel=REL)
    assert resultado.total_interest == pytest.approx(2_656_029.64, rel=REL)
    # Valor final e juros são arredondados a 2 casas, como o ROUND das células.
    assert resultado.final_balance == round(resultado.final_balance, 2)
    assert resultado.total_interest == round(resultado.total_interest, 2)


def test_simulador_serie_mensal_reproduz_a_evolucao() -> None:
    serie = compute_simulation(_config_simulador(), TAXA_ESPERADA).series

    assert len(serie) == 49  # mês 0 (foto de hoje) + 48 meses simulados
    assert serie[0].month == 0
    assert serie[0].balance == pytest.approx(2_400_000.0, rel=REL)
    assert serie[0].invested == pytest.approx(2_400_000.0, rel=REL)
    assert serie[0].interest == pytest.approx(0.0, abs=1e-9)

    assert serie[1].balance == pytest.approx(2_541_339.142789443, rel=REL)
    assert serie[2].balance == pytest.approx(2_683_934.97564127, rel=REL)
    assert serie[3].balance == pytest.approx(2_827_798.672175766, rel=REL)

    # Investido cresce em degraus de um aporte; juros é a diferença.
    assert serie[1].invested == pytest.approx(2_520_000.0, rel=REL)
    assert serie[1].interest == pytest.approx(21_339.142789443, rel=1e-8)


def test_simulador_serie_converge_para_a_formula_fechada() -> None:
    """A recursão e a fórmula fechada descrevem a mesma anuidade."""
    resultado = compute_simulation(_config_simulador(), TAXA_ESPERADA)
    assert resultado.series[-1].balance == pytest.approx(resultado.final_balance, rel=1e-9)


# --------------------------------------------------------------------------
# Simulador — ramos alternativos
# --------------------------------------------------------------------------


def test_simulador_taxa_zero_vira_soma_simples() -> None:
    """Sem juros a fórmula fechada degeneraria numa divisão por zero."""
    resultado = compute_simulation(_config_simulador(), 0.0, months=10)

    assert resultado.monthly_rate == 0.0
    assert resultado.final_balance == pytest.approx(2_400_000.0 + 120_000.0 * 10, rel=REL)
    assert resultado.total_interest == pytest.approx(0.0, abs=1e-9)
    assert resultado.series[-1].balance == pytest.approx(resultado.final_balance, rel=REL)


def test_simulador_retirada_maior_que_aporte_gera_pmt_negativo() -> None:
    parametros = Assumptions(
        initial_capital=1_000_000.0,
        monthly_contribution=1_000.0,
        monthly_withdrawal=6_000.0,
    )
    resultado = compute_simulation(parametros, 0.0, months=12)

    assert resultado.monthly == pytest.approx(-5_000.0, rel=REL)
    assert resultado.final_balance == pytest.approx(940_000.0, rel=REL)
    assert resultado.total_invested == pytest.approx(940_000.0, rel=REL)
    assert resultado.series[1].balance == pytest.approx(995_000.0, rel=REL)


def test_simulador_deriva_os_meses_das_datas_do_config() -> None:
    """4,46 anos entre as datas arredondam para 4 anos → 48 meses."""
    parametros = Assumptions(
        initial_capital=1.0,
        start_date=date(2026, 7, 17),
        end_date=date(2030, 12, 31),
    )
    assert compute_simulation(parametros, TAXA_ESPERADA).months == 48
    # O argumento explícito tem precedência sobre as datas.
    assert compute_simulation(parametros, TAXA_ESPERADA, months=7).months == 7


def test_simulador_horizonte_invalido_cai_no_padrao() -> None:
    # Datas invertidas não descrevem horizonte nenhum.
    invertido = Assumptions(start_date=date(2030, 1, 1), end_date=date(2026, 1, 1))
    assert compute_simulation(invertido, 0.10).months == 48
    # Intervalo menor que meio ano arredonda para 0 ano — idem.
    curto = Assumptions(start_date=date(2026, 1, 1), end_date=date(2026, 3, 1))
    assert compute_simulation(curto, 0.10).months == 48
    # Sem datas: padrão da planilha.
    assert compute_simulation(Assumptions(), 0.10).months == 48


# --------------------------------------------------------------------------
# Simulador — casos degenerados (nunca levantam exceção)
# --------------------------------------------------------------------------


def test_simulador_sem_parametros_devolve_zeros() -> None:
    resultado = compute_simulation(Assumptions(), 0.0)

    assert resultado.final_balance == 0.0
    assert resultado.total_invested == 0.0
    assert resultado.total_interest == 0.0
    assert len(resultado.series) == 49
    assert all(ponto.balance == 0.0 for ponto in resultado.series)


def test_simulador_meses_zero_ou_negativo_nao_quebra() -> None:
    zero = compute_simulation(_config_simulador(), TAXA_ESPERADA, months=0)
    assert zero.months == 0
    assert zero.final_balance == pytest.approx(2_400_000.0, rel=REL)
    assert [ponto.month for ponto in zero.series] == [0]

    negativo = compute_simulation(_config_simulador(), TAXA_ESPERADA, months=-12)
    assert negativo.months == 0


def test_simulador_tolera_campos_nulos_e_taxas_absurdas() -> None:
    nulos = Assumptions(
        initial_capital=None,  # type: ignore[arg-type]
        monthly_contribution=None,  # type: ignore[arg-type]
        monthly_withdrawal="",  # type: ignore[arg-type]
    )
    resultado = compute_simulation(nulos, None, months=3)  # type: ignore[arg-type]
    assert resultado.final_balance == 0.0
    assert resultado.monthly_rate == 0.0

    # Perda total (-100% a.a.) não pode virar raiz de número negativo.
    perda = compute_simulation(_config_simulador(), -1.0, months=6)
    assert perda.monthly_rate == -1.0
    assert perda.final_balance == pytest.approx(120_000.0, rel=REL)

    # Taxa astronômica estoura o expoente: devolvemos 0.0 em vez de propagar.
    estouro = compute_simulation(_config_simulador(), 1e300, months=1200)
    assert estouro.final_balance == 0.0

    assert taxa_mensal(-2.0) == -1.0
    assert taxa_mensal(float("nan")) == 0.0


# --------------------------------------------------------------------------
# Projeção — retorno anual de cada regime
# --------------------------------------------------------------------------


def test_annual_returns_reproduz_os_dois_regimes() -> None:
    renda, valorizacao = annual_returns(_config_projecao())

    assert renda == pytest.approx(0.11689050000000001, rel=REL)
    assert valorizacao == pytest.approx(0.16504250000000004, rel=REL)
    assert valorizacao > renda  # a carteira de crescimento embute os prêmios


def test_annual_returns_pesos_somam_um() -> None:
    """Se toda fatia render 10%, o regime de renda rende exatamente 10%."""
    uniforme = Assumptions(
        selic_annual=0.10,
        inflation_annual=0.04,
        real_rate_annual=0.06,  # ipca + real = 0,10
        dy_fii=0.07,
        price_return_fii=0.03,  # FIIs = 0,10
        dy_stocks=0.04,
        price_return_stocks=0.06,  # ações = 0,10
        foreign_return=0.10,
        duration_years=0.0,
        real_rate_delta=0.0,
    )
    renda, valorizacao = annual_returns(uniforme)

    assert renda == pytest.approx(0.10, rel=REL)
    # Valorização soma os prêmios: 0,20×3pp + 0,305×2pp + 0,10×3pp = 1,51pp.
    assert valorizacao == pytest.approx(0.10 + 0.0151, rel=REL)


def test_annual_returns_marcacao_a_mercado_do_ipca_longo() -> None:
    """Corte de 1 p.p. na taxa real valoriza o IPCA+ longo em duration × 1 p.p."""
    base = Assumptions(real_rate_delta=0.0, duration_years=24.0)
    corte = Assumptions(real_rate_delta=-0.01, duration_years=24.0)

    _, sem_corte = annual_returns(base)
    _, com_corte = annual_returns(corte)
    assert com_corte - sem_corte == pytest.approx(0.15 * 0.24, rel=REL)
    # O regime de renda carrega até o vencimento: marcação não o afeta.
    assert annual_returns(base)[0] == pytest.approx(annual_returns(corte)[0], rel=REL)


def test_annual_returns_sem_parametros_devolve_zeros() -> None:
    vazio = Assumptions(
        selic_annual=0.0,
        inflation_annual=0.0,
        real_rate_annual=0.0,
        real_rate_delta=0.0,
        duration_years=0.0,
        dy_fii=0.0,
        dy_stocks=0.0,
        price_return_stocks=0.0,
        price_return_fii=0.0,
        foreign_return=0.0,
    )
    renda, valorizacao = annual_returns(vazio)
    assert renda == pytest.approx(0.0, abs=1e-12)
    assert valorizacao == pytest.approx(0.0151, rel=REL)  # só os prêmios fixos


# --------------------------------------------------------------------------
# Projeção — série mensal
# --------------------------------------------------------------------------


def test_projecao_reproduz_as_primeiras_linhas_da_planilha() -> None:
    resultado = compute_projection(_config_projecao())

    assert resultado.annual_return_income == pytest.approx(0.11689050000000001, rel=REL)
    assert resultado.annual_return_growth == pytest.approx(0.16504250000000004, rel=REL)

    primeiro = resultado.series[0]
    assert primeiro.month == date(2026, 3, 1)
    assert primeiro.contribution == pytest.approx(120_000.0, rel=REL)
    # A primeira linha já é o capital inicial rendendo um mês + o aporte.
    assert primeiro.value_income == pytest.approx(2_582_582.04935004, rel=REL)
    assert primeiro.value_growth == pytest.approx(2_591_259.2451011105, rel=REL)
    assert primeiro.income_income == pytest.approx(18_707.34, rel=REL)

    segundo = resultado.series[1]
    assert segundo.month == date(2026, 4, 1)
    assert segundo.value_income == pytest.approx(2_726_483.6867635446, rel=REL)
    assert segundo.value_growth == pytest.approx(2_744_456.297504035, rel=REL)
    # A renda alvo cresce pela inflação mensal efetiva.
    assert segundo.income_income == pytest.approx(18_770.68711526948, rel=REL)


def test_projecao_horizonte_inclui_o_mes_final() -> None:
    resultado = compute_projection(_config_projecao())

    assert len(resultado.series) == 58  # mar/2026 → dez/2030, inclusive
    assert resultado.series[-1].month == date(2030, 12, 1)
    # Meses consecutivos, virando o ano corretamente.
    assert resultado.series[9].month == date(2026, 12, 1)
    assert resultado.series[10].month == date(2027, 1, 1)

    # O argumento explícito tem precedência sobre as datas.
    assert len(compute_projection(_config_projecao(), months=5).series) == 5
    # Sem datas: padrão da planilha.
    assert len(compute_projection(Assumptions()).series) == 58
    assert compute_projection(Assumptions()).series[0].month == date.today().replace(day=1)


def test_projecao_cenario_selecionado_segue_o_config() -> None:
    valorizacao = compute_projection(_config_projecao())
    assert valorizacao.scenario == "Valorizacao"
    assert valorizacao.series[0].value_selected == valorizacao.series[0].value_growth
    assert valorizacao.final_value == valorizacao.series[-1].value_growth

    renda_config = Assumptions(
        scenario="renda",  # comparação sem acento e sem caixa
        initial_capital=2_440_000.0,
        monthly_contribution=120_000.0,
        monthly_income_target=18_707.34,
    )
    renda = compute_projection(renda_config, months=3)
    assert renda.series[0].value_selected == renda.series[0].value_income
    assert renda.final_value == renda.series[-1].value_income
    assert renda.final_income == renda.series[-1].income_income


def test_projecao_sem_datas_infere_o_inicio_a_partir_do_fim() -> None:
    resultado = compute_projection(Assumptions(end_date=date(2030, 12, 31)), months=4)
    assert [ponto.month for ponto in resultado.series] == [
        date(2030, 9, 1),
        date(2030, 10, 1),
        date(2030, 11, 1),
        date(2030, 12, 1),
    ]


def test_projecao_degenerada_nao_quebra() -> None:
    vazia = compute_projection(_config_projecao(), months=0)
    assert vazia.series == []
    assert vazia.final_value == 0.0
    assert vazia.final_income == 0.0

    nulos = Assumptions(
        initial_capital=None,  # type: ignore[arg-type]
        monthly_contribution=None,  # type: ignore[arg-type]
        monthly_income_target=None,  # type: ignore[arg-type]
        scenario=None,  # type: ignore[arg-type]
        inflation_annual=float("nan"),
    )
    resultado = compute_projection(nulos, months=3)
    assert resultado.scenario == ""
    assert all(ponto.value_income == 0.0 for ponto in resultado.series)
    assert all(ponto.income_income == 0.0 for ponto in resultado.series)

    # Horizonte negativo vira série vazia; datas invertidas caem no padrão.
    assert compute_projection(Assumptions(), months=-3).series == []
    invertido = Assumptions(start_date=date(2030, 1, 1), end_date=date(2026, 1, 1))
    assert len(compute_projection(invertido).series) == 58
