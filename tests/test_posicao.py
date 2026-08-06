"""Testes de ``engine.calc.posicao`` — fixtures sintéticas, nunca dados reais."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc.posicao import compute_portfolio  # noqa: E402
from engine.models import Position, Provento, WorkbookInputs  # noqa: E402

REL = 1e-9


# --------------------------------------------------------------------------
# Fixtures sintéticas
# --------------------------------------------------------------------------


def _posicoes() -> List[Position]:
    """Carteira de 7 posições com valores redondos, conferíveis à mão."""
    return [
        Position("Ações", "PETR4", "Petrobras PN", 100.0, 30.0, var_12m=0.10),  # 3.000
        Position("Ações", "BBAS3", "Banco do Brasil", 200.0, 25.0, var_12m=-0.05),  # 5.000
        Position("FIIs", "XPML11", "XP Malls", 50.0, 100.0, var_12m=0.20),  # 5.000
        Position("FIIs", "KNRI11", "Kinea Renda", 10.0, 150.0, var_12m=None),  # 1.500
        Position("ETFs", "BOVA11", "Ibovespa", 20.0, 100.0, var_12m=0.08),  # 2.000
        Position("Tesouro Direto", "", "Tesouro IPCA+ 2050", 1.0, 4000.0),  # 4.000
        Position("Renda Fixa privada", "", "CDB XP", 1.0, 1000.0),  # 1.000
    ]


def _proventos() -> List[Provento]:
    """Inclui ticker minúsculo, com espaço, sem casamento e sem ticker."""
    return [
        Provento(date(2025, 3, 10), "Dividendo", "petr4 ", "Petrobras", 100.0),
        Provento(date(2025, 4, 10), "JCP", "PETR4", "Petrobras", 50.0),
        Provento(date(2025, 4, 15), "Rendimento", " xpml11", "XP Malls", 80.0),
        Provento(date(2025, 5, 20), "Rendimento", "CRA", "CRA Riza", 999.0),  # sem posição
        Provento(date(2025, 5, 21), "Rendimento", "", "CRI genérico", 5.0),  # sem ticker
        Provento(date(2025, 6, 1), "Dividendo", "BBAS3", "Banco do Brasil", 25.0),
    ]


def _inputs() -> WorkbookInputs:
    return WorkbookInputs(positions=_posicoes(), proventos=_proventos())


TOTAL = 21_500.0


# --------------------------------------------------------------------------
# Valor de mercado e pesos
# --------------------------------------------------------------------------


def test_valor_de_mercado_e_total() -> None:
    resultado = compute_portfolio(_inputs())

    assert resultado.total_value == pytest.approx(TOTAL, rel=REL)
    assert resultado.positions_count == 7

    por_ticker = {p.ticker: p for p in resultado.positions if p.ticker}
    assert por_ticker["PETR4"].market_value == pytest.approx(3000.0, rel=REL)
    assert por_ticker["BBAS3"].market_value == pytest.approx(5000.0, rel=REL)
    assert por_ticker["KNRI11"].market_value == pytest.approx(1500.0, rel=REL)


def test_peso_por_posicao_soma_um() -> None:
    resultado = compute_portfolio(_inputs())

    por_ticker = {p.ticker: p for p in resultado.positions if p.ticker}
    assert por_ticker["PETR4"].portfolio_weight == pytest.approx(3000.0 / TOTAL, rel=REL)
    assert por_ticker["BOVA11"].portfolio_weight == pytest.approx(2000.0 / TOTAL, rel=REL)
    assert sum(p.portfolio_weight for p in resultado.positions) == pytest.approx(1.0, rel=REL)


def test_posicoes_ordenadas_por_valor_decrescente() -> None:
    resultado = compute_portfolio(_inputs())

    valores = [p.market_value for p in resultado.positions]
    assert valores == sorted(valores, reverse=True)
    # Empate em 5.000: a ordenação é estável e preserva a ordem da planilha.
    assert [p.ticker for p in resultado.positions] == [
        "BBAS3",
        "XPML11",
        "",  # Tesouro IPCA+ 2050 (4.000)
        "PETR4",
        "BOVA11",
        "KNRI11",
        "",  # CDB XP (1.000)
    ]


# --------------------------------------------------------------------------
# Proventos (SUMIF)
# --------------------------------------------------------------------------


def test_proventos_casados_por_ticker() -> None:
    resultado = compute_portfolio(_inputs())
    por_ticker = {p.ticker: p for p in resultado.positions if p.ticker}

    # "petr4 " + "PETR4" somam na mesma posição: casamento ignora caixa e espaços.
    assert por_ticker["PETR4"].proventos_period == pytest.approx(150.0, rel=REL)
    assert por_ticker["XPML11"].proventos_period == pytest.approx(80.0, rel=REL)
    assert por_ticker["BBAS3"].proventos_period == pytest.approx(25.0, rel=REL)
    assert por_ticker["KNRI11"].proventos_period == 0.0


def test_total_de_proventos_ignora_lancamentos_sem_posicao() -> None:
    resultado = compute_portfolio(_inputs())

    # 999,00 (CRA, sem posição) e 5,00 (sem ticker) ficam de fora dos 1.259,00.
    assert resultado.total_proventos == pytest.approx(255.0, rel=REL)


def test_posicoes_sem_ticker_nao_recebem_proventos() -> None:
    resultado = compute_portfolio(_inputs())

    sem_ticker = [p for p in resultado.positions if not p.ticker]
    assert len(sem_ticker) == 2
    assert all(p.proventos_period == 0.0 for p in sem_ticker)


# --------------------------------------------------------------------------
# Resumo por classe
# --------------------------------------------------------------------------


def test_resumo_por_classe_ordenado_por_valor() -> None:
    resultado = compute_portfolio(_inputs())

    assert [c.asset_class for c in resultado.by_class] == [
        "Ações",
        "FIIs",
        "Tesouro Direto",
        "ETFs",
        "Renda Fixa privada",
    ]
    valores = {c.asset_class: c.value for c in resultado.by_class}
    assert valores["Ações"] == pytest.approx(8000.0, rel=REL)
    assert valores["FIIs"] == pytest.approx(6500.0, rel=REL)
    assert valores["Tesouro Direto"] == pytest.approx(4000.0, rel=REL)
    assert valores["ETFs"] == pytest.approx(2000.0, rel=REL)
    assert valores["Renda Fixa privada"] == pytest.approx(1000.0, rel=REL)


def test_resumo_por_classe_conta_posicoes_e_pesos() -> None:
    resultado = compute_portfolio(_inputs())

    contagens = {c.asset_class: c.positions for c in resultado.by_class}
    assert contagens == {
        "Ações": 2,
        "FIIs": 2,
        "ETFs": 1,
        "Tesouro Direto": 1,
        "Renda Fixa privada": 1,
    }
    pesos = {c.asset_class: c.weight for c in resultado.by_class}
    assert pesos["Ações"] == pytest.approx(8000.0 / TOTAL, rel=REL)
    assert sum(c.weight for c in resultado.by_class) == pytest.approx(1.0, rel=REL)
    assert sum(c.positions for c in resultado.by_class) == resultado.positions_count


def test_classe_vazia_vira_outros() -> None:
    inputs = WorkbookInputs(positions=[Position("", "ABC3", "Sem classe", 10.0, 5.0)])
    resultado = compute_portfolio(inputs)

    assert [c.asset_class for c in resultado.by_class] == ["Outros"]
    assert resultado.positions[0].asset_class == "Outros"


# --------------------------------------------------------------------------
# Variação ponderada de 12 meses
# --------------------------------------------------------------------------


def test_var_12m_ponderada_renormaliza_entre_quem_tem_variacao() -> None:
    resultado = compute_portfolio(_inputs())

    # Numerador: 3.000×0,10 + 5.000×(-0,05) + 5.000×0,20 + 2.000×0,08 = 1.210
    # Denominador: 15.000 (KNRI11 sem var_12m, Tesouro e RF fora por classe)
    assert resultado.weighted_var_12m == pytest.approx(1210.0 / 15000.0, rel=REL)


def test_var_12m_ignora_classes_nao_listadas() -> None:
    """Um Tesouro com var_12m preenchida não pode entrar na conta."""
    posicoes = _posicoes()
    posicoes[5] = Position("Tesouro Direto", "", "Tesouro IPCA+ 2050", 1.0, 4000.0, var_12m=0.99)
    resultado = compute_portfolio(WorkbookInputs(positions=posicoes))

    assert resultado.weighted_var_12m == pytest.approx(1210.0 / 15000.0, rel=REL)


def test_var_12m_aceita_classe_sem_acento_ou_caixa_diferente() -> None:
    posicoes = [
        Position("ACOES", "PETR4", "Petrobras", 100.0, 30.0, var_12m=0.10),
        Position("fiis", "XPML11", "XP Malls", 50.0, 100.0, var_12m=0.20),
    ]
    resultado = compute_portfolio(WorkbookInputs(positions=posicoes))

    esperado = (3000.0 * 0.10 + 5000.0 * 0.20) / 8000.0
    assert resultado.weighted_var_12m == pytest.approx(esperado, rel=REL)


def test_var_12m_cai_para_todas_as_posicoes_quando_nenhuma_classe_e_listada() -> None:
    """Planilha genérica ('Outros'): usa o que houver em vez de devolver 0."""
    posicoes = [
        Position("Outros", "AAA3", "A", 10.0, 100.0, var_12m=0.10),
        Position("Outros", "BBB3", "B", 10.0, 300.0, var_12m=0.20),
    ]
    resultado = compute_portfolio(WorkbookInputs(positions=posicoes))

    esperado = (1000.0 * 0.10 + 3000.0 * 0.20) / 4000.0
    assert resultado.weighted_var_12m == pytest.approx(esperado, rel=REL)


def test_var_12m_zero_quando_ninguem_tem_variacao() -> None:
    posicoes = [Position("Ações", "PETR4", "Petrobras", 100.0, 30.0, var_12m=None)]
    resultado = compute_portfolio(WorkbookInputs(positions=posicoes))

    assert resultado.weighted_var_12m == 0.0


# --------------------------------------------------------------------------
# Casos degenerados: nunca levantar exceção
# --------------------------------------------------------------------------


def test_carteira_vazia() -> None:
    resultado = compute_portfolio(WorkbookInputs())

    assert resultado.total_value == 0.0
    assert resultado.total_proventos == 0.0
    assert resultado.positions_count == 0
    assert resultado.weighted_var_12m == 0.0
    assert resultado.positions == []
    assert resultado.by_class == []


def test_inputs_none_nao_quebra() -> None:
    resultado = compute_portfolio(None)  # type: ignore[arg-type]

    assert resultado.total_value == 0.0
    assert resultado.positions == []
    assert resultado.by_class == []


def test_total_zero_nao_divide_por_zero() -> None:
    posicoes = [
        Position("Ações", "PETR4", "Petrobras", 100.0, 0.0, var_12m=0.10),
        Position("FIIs", "XPML11", "XP Malls", 0.0, 100.0, var_12m=0.20),
    ]
    resultado = compute_portfolio(WorkbookInputs(positions=posicoes))

    assert resultado.total_value == 0.0
    assert all(p.portfolio_weight == 0.0 for p in resultado.positions)
    assert all(c.weight == 0.0 for c in resultado.by_class)
    assert resultado.weighted_var_12m == 0.0  # denominador zerado


def test_campos_nulos_e_nan_viram_zero() -> None:
    posicoes = [
        Position("Ações", "PETR4", "Petrobras", None, None, var_12m=float("nan")),  # type: ignore[arg-type]
        Position("FIIs", "XPML11", "XP Malls", 10.0, 100.0, var_12m=0.20),
    ]
    proventos = [
        None,  # type: ignore[list-item]
        Provento(None, "Rendimento", "XPML11", "XP Malls", None),  # type: ignore[arg-type]
        Provento(None, "Rendimento", "PETR4", "Petrobras", 40.0),
    ]
    resultado = compute_portfolio(WorkbookInputs(positions=posicoes, proventos=proventos))

    por_ticker = {p.ticker: p for p in resultado.positions}
    assert por_ticker["PETR4"].market_value == 0.0
    assert por_ticker["PETR4"].var_12m is None  # NaN não é dado
    assert por_ticker["XPML11"].proventos_period == 0.0  # valor None
    assert resultado.total_value == pytest.approx(1000.0, rel=REL)
    assert resultado.total_proventos == pytest.approx(40.0, rel=REL)
    assert resultado.weighted_var_12m == pytest.approx(0.20, rel=REL)


def test_posicao_none_na_lista_e_ignorada() -> None:
    posicoes = [None, Position("Ações", "PETR4", "Petrobras", 10.0, 10.0)]  # type: ignore[list-item]
    resultado = compute_portfolio(WorkbookInputs(positions=posicoes))

    assert resultado.positions_count == 1
    assert resultado.total_value == pytest.approx(100.0, rel=REL)


def test_ticker_repetido_nao_duplica_o_total_de_proventos() -> None:
    """Mesmo ticker em duas corretoras: cada linha vê o SUMIF, o total não dobra."""
    posicoes = [
        Position("Ações", "PETR4", "Petrobras (XP)", 100.0, 30.0),
        Position("Ações", "PETR4", "Petrobras (C6)", 50.0, 30.0),
    ]
    proventos = [Provento(date(2025, 3, 10), "Dividendo", "PETR4", "Petrobras", 100.0)]
    resultado = compute_portfolio(WorkbookInputs(positions=posicoes, proventos=proventos))

    assert all(p.proventos_period == pytest.approx(100.0, rel=REL) for p in resultado.positions)
    assert resultado.total_proventos == pytest.approx(100.0, rel=REL)


def test_campos_de_texto_e_indicadores_sao_preservados() -> None:
    posicoes = [
        Position(
            "Ações",
            "BBAS3",
            "Banco do Brasil",
            100.0,
            25.0,
            var_12m=-0.05,
            note="Não atualizar",
            price_earnings=4.2,
            price_to_book=0.8,
            dividend_yield=0.11,
            technical_signal="Sobrevendido",
            fundamental_read="Barato",
        )
    ]
    resultado = compute_portfolio(WorkbookInputs(positions=posicoes))
    posicao = resultado.positions[0]

    assert posicao.name == "Banco do Brasil"
    assert posicao.note == "Não atualizar"
    assert posicao.price_earnings == pytest.approx(4.2, rel=REL)
    assert posicao.price_to_book == pytest.approx(0.8, rel=REL)
    assert posicao.dividend_yield == pytest.approx(0.11, rel=REL)
    assert posicao.technical_signal == "Sobrevendido"
    assert posicao.fundamental_read == "Barato"
