"""Testes de ``engine.calc.cenarios`` — fixtures sintéticas, nunca dados reais."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc.cenarios import (  # noqa: E402
    AVISO_CLASSES_DERIVADAS,
    CENARIOS_PADRAO,
    compute_scenarios,
)
from engine.calc.posicao import compute_portfolio  # noqa: E402
from engine.models import (  # noqa: E402
    ClassSummary,
    PortfolioResult,
    Position,
    ScenarioClass,
    WorkbookInputs,
)

REL = 1e-9

PESSIMO, RUIM, OTIMISTA, FAVORAVEL = CENARIOS_PADRAO


# --------------------------------------------------------------------------
# Fixtures sintéticas
# --------------------------------------------------------------------------


def _classes() -> List[ScenarioClass]:
    """Matriz de 7 classes com pesos que somam 1 e retornos de conta redonda."""
    return [
        ScenarioClass("Ações", 0.30, {PESSIMO: -0.04, RUIM: 0.05, OTIMISTA: 0.14, FAVORAVEL: 0.22}),
        ScenarioClass("FIIs", 0.35, {PESSIMO: 0.02, RUIM: 0.07, OTIMISTA: 0.14, FAVORAVEL: 0.20}),
        ScenarioClass("ETFs", 0.05, {PESSIMO: -0.01, RUIM: 0.05, OTIMISTA: 0.11, FAVORAVEL: 0.16}),
        ScenarioClass("BDRs", 0.01, {PESSIMO: -0.05, RUIM: 0.04, OTIMISTA: 0.12, FAVORAVEL: 0.20}),
        ScenarioClass(
            "Fundos Listados", 0.02, {PESSIMO: 0.02, RUIM: 0.07, OTIMISTA: 0.11, FAVORAVEL: 0.14}
        ),
        ScenarioClass(
            "RF Privada", 0.15, {PESSIMO: 0.13, RUIM: 0.12, OTIMISTA: 0.105, FAVORAVEL: 0.095}
        ),
        ScenarioClass(
            "Tesouro Direto", 0.12, {PESSIMO: 0.125, RUIM: 0.11, OTIMISTA: 0.105, FAVORAVEL: 0.10}
        ),
    ]


def _probabilidades() -> Dict[str, float]:
    return {PESSIMO: 0.10, RUIM: 0.30, OTIMISTA: 0.40, FAVORAVEL: 0.20}


def _inflacao() -> List[Dict[str, float]]:
    return [
        {"year": 2026, "ipca": 0.0516, "selic": 0.14},
        {"year": 2027, "ipca": 0.042, "selic": 0.12},
        {"year": 2028, "ipca": 0.037, "selic": 0.105},
        {"year": 2029, "ipca": 0.035, "selic": 0.10},
    ]


def _posicoes() -> List[Position]:
    """Ações e FIIs com indicadores; o FII sem DY/P-VP é o caso do SUMIFS."""
    return [
        # Ações: 3.000 e 5.000 → total 8.000
        Position("Ações", "PETR4", "Petrobras PN", 100.0, 30.0, dividend_yield=0.10, price_earnings=5.0),
        Position("Ações", "BBAS3", "Banco do Brasil", 200.0, 25.0, dividend_yield=0.08, price_earnings=4.0),
        # FIIs: 5.000 (com indicadores) e 1.500 (sem nenhum indicador)
        Position("FIIs", "XPML11", "XP Malls", 50.0, 100.0, dividend_yield=0.12, price_to_book=0.90),
        Position("FIIs", "KNRI11", "Kinea Renda", 10.0, 150.0),
        # Classes fora do recorte não podem contaminar as médias.
        Position("ETFs", "BOVA11", "Ibovespa", 20.0, 100.0, dividend_yield=0.99, price_earnings=99.0),
        Position("Tesouro Direto", "", "Tesouro IPCA+ 2050", 1.0, 4000.0),
    ]


def _inputs(**overrides: object) -> WorkbookInputs:
    base = {
        "positions": _posicoes(),
        "scenario_classes": _classes(),
        "scenario_probabilities": _probabilidades(),
        "inflation_forecast": _inflacao(),
    }
    base.update(overrides)
    return WorkbookInputs(**base)  # type: ignore[arg-type]


def _resultado(**overrides: object):
    inputs = _inputs(**overrides)
    return compute_scenarios(inputs, compute_portfolio(inputs)), inputs


# --------------------------------------------------------------------------
# Retorno por cenário e retorno esperado
# --------------------------------------------------------------------------


def test_retorno_por_cenario_e_sumproduct_peso_x_retorno() -> None:
    resultado, _ = _resultado()
    esperado = {
        PESSIMO: 0.30 * -0.04 + 0.35 * 0.02 + 0.05 * -0.01 + 0.01 * -0.05
        + 0.02 * 0.02 + 0.15 * 0.13 + 0.12 * 0.125,
        RUIM: 0.30 * 0.05 + 0.35 * 0.07 + 0.05 * 0.05 + 0.01 * 0.04
        + 0.02 * 0.07 + 0.15 * 0.12 + 0.12 * 0.11,
        OTIMISTA: 0.30 * 0.14 + 0.35 * 0.14 + 0.05 * 0.11 + 0.01 * 0.12
        + 0.02 * 0.11 + 0.15 * 0.105 + 0.12 * 0.105,
        FAVORAVEL: 0.30 * 0.22 + 0.35 * 0.20 + 0.05 * 0.16 + 0.01 * 0.20
        + 0.02 * 0.14 + 0.15 * 0.095 + 0.12 * 0.10,
    }
    assert list(resultado.returns_by_scenario) == list(CENARIOS_PADRAO)
    for cenario, alvo in esperado.items():
        assert resultado.returns_by_scenario[cenario] == pytest.approx(alvo, rel=REL)


def test_retorno_esperado_pondera_pelas_probabilidades() -> None:
    resultado, _ = _resultado()
    alvo = sum(
        resultado.returns_by_scenario[cenario] * probabilidade
        for cenario, probabilidade in _probabilidades().items()
    )
    assert resultado.expected_return == pytest.approx(alvo, rel=REL)
    assert resultado.probabilities == _probabilidades()


def test_paridade_com_a_planilha_real() -> None:
    """Pesos e retornos idênticos aos da aba Cenarios reproduzem os números do Excel."""
    classes = [
        ScenarioClass(
            "Ações", 0.3229295101950529, {PESSIMO: -0.04, RUIM: 0.05, OTIMISTA: 0.14, FAVORAVEL: 0.22}
        ),
        ScenarioClass(
            "FIIs", 0.3539376985227651, {PESSIMO: 0.02, RUIM: 0.07, OTIMISTA: 0.14, FAVORAVEL: 0.20}
        ),
        ScenarioClass(
            "ETFs", 0.03246372860176506, {PESSIMO: -0.01, RUIM: 0.05, OTIMISTA: 0.11, FAVORAVEL: 0.16}
        ),
        ScenarioClass(
            "BDRs", 0.007904470773117072, {PESSIMO: -0.05, RUIM: 0.04, OTIMISTA: 0.12, FAVORAVEL: 0.20}
        ),
        ScenarioClass(
            "Fundos Listados",
            0.024142521537078142,
            {PESSIMO: 0.02, RUIM: 0.07, OTIMISTA: 0.11, FAVORAVEL: 0.14},
        ),
        ScenarioClass(
            "RF Privada",
            0.13572502300216113,
            {PESSIMO: 0.13, RUIM: 0.12, OTIMISTA: 0.105, FAVORAVEL: 0.095},
        ),
        ScenarioClass(
            "Tesouro Direto",
            0.12289704736806081,
            {PESSIMO: 0.125, RUIM: 0.11, OTIMISTA: 0.105, FAVORAVEL: 0.10},
        ),
    ]
    resultado, _ = _resultado(scenario_classes=classes)

    assert resultado.returns_by_scenario[PESSIMO] == pytest.approx(0.026930947080009794, rel=REL)
    assert resultado.returns_by_scenario[RUIM] == pytest.approx(0.07435713414570064, rel=REL)
    assert resultado.returns_by_scenario[OTIMISTA] == pytest.approx(0.12909195061741463, rel=REL)
    assert resultado.returns_by_scenario[FAVORAVEL] == pytest.approx(0.17717065761557282, rel=REL)
    assert resultado.expected_return == pytest.approx(0.11207114672179158, rel=REL)
    assert resultado.inflation_average == pytest.approx(0.0414, rel=REL)
    assert resultado.selic_average == pytest.approx(0.11624999999999999, rel=REL)


def test_probabilidades_que_nao_somam_um_zeram_o_retorno_esperado() -> None:
    quebradas = {PESSIMO: 0.10, RUIM: 0.30, OTIMISTA: 0.40}  # falta o 0,20
    resultado, _ = _resultado(scenario_probabilities=quebradas)
    assert resultado.expected_return == 0.0
    # Os retornos por cenário continuam válidos — só a esperança é suprimida.
    assert resultado.returns_by_scenario[OTIMISTA] == pytest.approx(0.12825, rel=REL)


def test_probabilidades_dentro_da_tolerancia_ainda_valem() -> None:
    quase = {PESSIMO: 0.10, RUIM: 0.30, OTIMISTA: 0.40, FAVORAVEL: 0.2000005}
    resultado, _ = _resultado(scenario_probabilities=quase)
    assert resultado.expected_return != 0.0

    fora = {PESSIMO: 0.10, RUIM: 0.30, OTIMISTA: 0.40, FAVORAVEL: 0.2001}
    resultado_fora, _ = _resultado(scenario_probabilities=fora)
    assert resultado_fora.expected_return == 0.0


def test_sem_probabilidades_o_retorno_esperado_e_zero() -> None:
    resultado, _ = _resultado(scenario_probabilities={})
    assert resultado.expected_return == 0.0
    assert resultado.probabilities == {}


def test_cenario_so_nas_probabilidades_entra_com_retorno_zero() -> None:
    probabilidades = dict(_probabilidades())
    probabilidades[FAVORAVEL] = 0.10
    probabilidades["Catástrofe"] = 0.10
    resultado, _ = _resultado(scenario_probabilities=probabilidades)
    assert resultado.returns_by_scenario["Catástrofe"] == 0.0
    assert resultado.expected_return == pytest.approx(
        sum(
            resultado.returns_by_scenario[cenario] * probabilidade
            for cenario, probabilidade in probabilidades.items()
        ),
        rel=REL,
    )


# --------------------------------------------------------------------------
# Médias de inflação e Selic
# --------------------------------------------------------------------------


def test_medias_da_projecao_sao_simples() -> None:
    resultado, _ = _resultado()
    assert resultado.inflation_average == pytest.approx((0.0516 + 0.042 + 0.037 + 0.035) / 4, rel=REL)
    assert resultado.selic_average == pytest.approx((0.14 + 0.12 + 0.105 + 0.10) / 4, rel=REL)
    assert resultado.inflation_forecast == _inflacao()


def test_projecao_vazia_nao_divide_por_zero() -> None:
    resultado, _ = _resultado(inflation_forecast=[])
    assert resultado.inflation_average == 0.0
    assert resultado.selic_average == 0.0
    assert resultado.inflation_forecast == []


def test_projecao_sem_selic_ignora_a_linha_no_denominador() -> None:
    previsao = [{"year": 2026, "ipca": 0.05, "selic": 0.14}, {"year": 2027, "ipca": 0.03}]
    resultado, _ = _resultado(inflation_forecast=previsao)
    assert resultado.inflation_average == pytest.approx(0.04, rel=REL)
    assert resultado.selic_average == pytest.approx(0.14, rel=REL)


def test_projecao_nao_aliasa_a_entrada() -> None:
    previsao = _inflacao()
    resultado, _ = _resultado(inflation_forecast=previsao)
    resultado.inflation_forecast[0]["ipca"] = 9.99
    assert previsao[0]["ipca"] == 0.0516


# --------------------------------------------------------------------------
# Indicadores ponderados (SUMPRODUCT / SUMIFS)
# --------------------------------------------------------------------------


def test_dy_ponderado_de_acoes() -> None:
    resultado, _ = _resultado()
    # (3.000 × 0,10 + 5.000 × 0,08) / 8.000
    assert resultado.weighted_dy_stocks == pytest.approx(700.0 / 8_000.0, rel=REL)


def test_pl_ponderado_de_acoes() -> None:
    resultado, _ = _resultado()
    # (3.000 × 5 + 5.000 × 4) / 8.000
    assert resultado.weighted_pe_stocks == pytest.approx(35_000.0 / 8_000.0, rel=REL)


def test_ativo_sem_indicador_conta_no_denominador() -> None:
    """Comportamento do ``SUMPRODUCT(...)/SUMIFS(...)``: numerador 0, denominador cheio."""
    resultado, _ = _resultado()
    # FIIs: 5.000 com DY 0,12 e 1.500 sem DY → 600 / 6.500 (e não 600 / 5.000).
    assert resultado.weighted_dy_fii == pytest.approx(600.0 / 6_500.0, rel=REL)
    assert resultado.weighted_dy_fii != pytest.approx(0.12, rel=REL)
    # P/VP: 5.000 × 0,90 / 6.500.
    assert resultado.weighted_pb_fii == pytest.approx(4_500.0 / 6_500.0, rel=REL)


def test_classe_inexistente_devolve_zero() -> None:
    so_renda_fixa = [Position("Tesouro Direto", "", "Tesouro Selic", 1.0, 1_000.0)]
    resultado, _ = _resultado(positions=so_renda_fixa)
    assert resultado.weighted_dy_stocks == 0.0
    assert resultado.weighted_dy_fii == 0.0
    assert resultado.weighted_pe_stocks == 0.0
    assert resultado.weighted_pb_fii == 0.0


def test_classe_com_valor_de_mercado_zero_nao_estoura() -> None:
    zeradas = [
        Position("Ações", "PETR4", "Petrobras", 0.0, 0.0, dividend_yield=0.10, price_earnings=5.0),
        Position("FIIs", "XPML11", "XP Malls", 10.0, 0.0, price_to_book=0.9),
    ]
    resultado, _ = _resultado(positions=zeradas)
    assert resultado.weighted_dy_stocks == 0.0
    assert resultado.weighted_pb_fii == 0.0


def test_nomes_de_classe_tolerantes() -> None:
    variantes = [
        Position("AÇÕES ", "PETR4", "Petrobras", 100.0, 10.0, dividend_yield=0.10),
        Position("Fundos Imobiliários", "XPML11", "XP Malls", 100.0, 10.0, dividend_yield=0.12),
        Position("Fundos listados", "FUND11", "Fundo listado", 100.0, 10.0, dividend_yield=0.99),
    ]
    resultado, _ = _resultado(positions=variantes)
    assert resultado.weighted_dy_stocks == pytest.approx(0.10, rel=REL)
    # "Fundos listados" não pode ser confundido com FII.
    assert resultado.weighted_dy_fii == pytest.approx(0.12, rel=REL)


def test_indicadores_usam_a_carteira_quando_nao_ha_posicoes_de_entrada() -> None:
    inputs = _inputs(positions=[])
    portfolio = compute_portfolio(_inputs())
    resultado = compute_scenarios(inputs, portfolio)
    assert resultado.weighted_dy_stocks == pytest.approx(700.0 / 8_000.0, rel=REL)
    assert resultado.weighted_dy_fii == pytest.approx(600.0 / 6_500.0, rel=REL)


# --------------------------------------------------------------------------
# Matriz derivada da carteira e casos degenerados
# --------------------------------------------------------------------------


def test_sem_matriz_as_classes_vem_da_carteira_com_retorno_zero() -> None:
    resultado, inputs = _resultado(scenario_classes=[])
    portfolio = compute_portfolio(_inputs())

    assert [c.name for c in resultado.classes] == [s.asset_class for s in portfolio.by_class]
    assert [c.weight for c in resultado.classes] == [s.weight for s in portfolio.by_class]
    assert all(set(c.returns) == set(CENARIOS_PADRAO) for c in resultado.classes)
    assert all(retorno == 0.0 for c in resultado.classes for retorno in c.returns.values())
    assert all(valor == 0.0 for valor in resultado.returns_by_scenario.values())
    assert resultado.expected_return == 0.0
    assert AVISO_CLASSES_DERIVADAS in inputs.warnings


def test_aviso_de_classes_derivadas_nao_duplica() -> None:
    inputs = _inputs(scenario_classes=[])
    portfolio = compute_portfolio(inputs)
    compute_scenarios(inputs, portfolio)
    compute_scenarios(inputs, portfolio)
    assert inputs.warnings.count(AVISO_CLASSES_DERIVADAS) == 1


def test_classes_derivadas_seguem_os_cenarios_das_probabilidades() -> None:
    probabilidades = {"Base": 0.5, "Estresse": 0.5}
    resultado, _ = _resultado(scenario_classes=[], scenario_probabilities=probabilidades)
    assert all(set(c.returns) == {"Base", "Estresse"} for c in resultado.classes)
    assert list(resultado.returns_by_scenario) == ["Base", "Estresse"]
    assert resultado.expected_return == 0.0


def test_entrada_totalmente_vazia_devolve_zeros() -> None:
    vazio = PortfolioResult(0.0, 0.0, 0, 0.0, [], [])
    resultado = compute_scenarios(WorkbookInputs(), vazio)
    assert resultado.classes == []
    assert resultado.returns_by_scenario == {}
    assert resultado.probabilities == {}
    assert resultado.expected_return == 0.0
    assert resultado.inflation_average == 0.0
    assert resultado.selic_average == 0.0
    assert resultado.weighted_dy_stocks == 0.0
    assert resultado.weighted_dy_fii == 0.0
    assert resultado.weighted_pe_stocks == 0.0
    assert resultado.weighted_pb_fii == 0.0
    assert resultado.inflation_forecast == []


def test_carteira_nula_nao_levanta_excecao() -> None:
    resultado = compute_scenarios(_inputs(), None)  # type: ignore[arg-type]
    assert resultado.expected_return == pytest.approx(0.1117, rel=REL)
    assert resultado.weighted_dy_stocks == pytest.approx(700.0 / 8_000.0, rel=REL)


def test_campos_sujos_nao_levantam_excecao() -> None:
    sujas = [
        ScenarioClass("Ações", float("nan"), {PESSIMO: 0.10}),
        ScenarioClass("FIIs", 1.0, {PESSIMO: None, RUIM: float("inf")}),  # type: ignore[dict-item]
        ScenarioClass("", 0.5, {PESSIMO: 0.5}),  # sem nome: descartada
    ]
    resultado, _ = _resultado(
        scenario_classes=sujas, scenario_probabilities={PESSIMO: 1.0}, inflation_forecast=None
    )
    assert [c.name for c in resultado.classes] == ["Ações", "FIIs"]
    assert resultado.returns_by_scenario[PESSIMO] == 0.0
    assert resultado.returns_by_scenario[RUIM] == 0.0
    assert resultado.expected_return == 0.0
    assert resultado.inflation_average == 0.0


def test_classes_sem_o_cenario_entram_com_zero() -> None:
    parciais = [
        ScenarioClass("Ações", 0.5, {PESSIMO: -0.04, OTIMISTA: 0.14}),
        ScenarioClass("FIIs", 0.5, {PESSIMO: 0.02}),  # sem "Otimista"
    ]
    resultado, _ = _resultado(scenario_classes=parciais, scenario_probabilities={})
    assert resultado.returns_by_scenario[PESSIMO] == pytest.approx(-0.01, rel=REL)
    assert resultado.returns_by_scenario[OTIMISTA] == pytest.approx(0.07, rel=REL)


def test_resultado_e_estavel_entre_chamadas() -> None:
    inputs = _inputs()
    portfolio = compute_portfolio(inputs)
    primeiro = compute_scenarios(inputs, portfolio)
    segundo = compute_scenarios(inputs, portfolio)
    assert primeiro == segundo
    assert inputs.warnings == []


def test_classes_derivadas_ignoram_resumo_nulo() -> None:
    portfolio = PortfolioResult(
        total_value=1_000.0,
        total_proventos=0.0,
        positions_count=1,
        weighted_var_12m=0.0,
        positions=[],
        by_class=[ClassSummary("Ações", 1_000.0, 1.0, 1)],
    )
    inputs = WorkbookInputs(scenario_probabilities={PESSIMO: 0.5, RUIM: 0.5})
    resultado = compute_scenarios(inputs, portfolio)
    assert [c.name for c in resultado.classes] == ["Ações"]
    assert resultado.classes[0].weight == pytest.approx(1.0, rel=REL)
    assert resultado.returns_by_scenario == {PESSIMO: 0.0, RUIM: 0.0}
    assert resultado.expected_return == 0.0
