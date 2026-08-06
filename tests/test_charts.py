"""Testes de ``engine.calc.charts`` — fixtures sintéticas, nunca dados reais."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc.charts import (  # noqa: E402
    CHART_IDS,
    build_charts,
    chart_by_id,
)
from engine.models import (  # noqa: E402
    CashflowResult,
    ChartSeries,
    ClassSummary,
    ConsolidatedResult,
    MonthlyCashflow,
    PortfolioResult,
    PositionResult,
    ProjectionPoint,
    ProjectionResult,
    ProventosResult,
    SimulationPoint,
    SimulationResult,
    to_dict,
)

REL = 1e-9


# --------------------------------------------------------------------------
# Fixtures sintéticas
# --------------------------------------------------------------------------


def _posicao(
    ticker: str,
    classe: str,
    valor: float,
    var_12m: Optional[float] = None,
    peso: float = 0.0,
) -> PositionResult:
    return PositionResult(
        ticker=ticker,
        name=f"{ticker} S.A.",
        asset_class=classe,
        quantity=100.0,
        price=valor / 100.0,
        market_value=valor,
        portfolio_weight=peso,
        proventos_period=0.0,
        var_12m=var_12m,
        price_earnings=None,
        price_to_book=None,
        dividend_yield=None,
        technical_signal="",
        fundamental_read="",
        note="",
    )


def _posicoes() -> List[PositionResult]:
    """12 posições: 10 com var_12m (para 5+5) e 2 sem o dado."""
    dados = [
        ("AAAA1", "Ações", 12_000.0, 0.50),
        ("BBBB1", "Ações", 11_000.0, 0.40),
        ("CCCC1", "FIIs", 10_000.0, 0.30),
        ("DDDD1", "FIIs", 9_000.0, 0.20),
        ("EEEE1", "ETFs", 8_000.0, 0.10),
        ("FFFF1", "Ações", 7_000.0, 0.05),
        ("GGGG1", "FIIs", 6_000.0, -0.05),
        ("HHHH1", "BDRs", 5_000.0, -0.10),
        ("IIII1", "ETFs", 4_000.0, -0.20),
        ("JJJJ1", "Ações", 3_000.0, -0.30),
        ("KKKK1", "Tesouro Direto", 2_000.0, None),
        ("LLLL1", "Renda Fixa privada", 1_000.0, None),
    ]
    total = sum(valor for _, _, valor, _ in dados)
    return [
        _posicao(ticker, classe, valor, var, peso=valor / total)
        for ticker, classe, valor, var in dados
    ]


def _carteira() -> PortfolioResult:
    posicoes = _posicoes()
    total = sum(p.market_value for p in posicoes)
    classes: Dict[str, List[float]] = {}
    for posicao in posicoes:
        acumulado = classes.setdefault(posicao.asset_class, [0.0, 0.0])
        acumulado[0] += posicao.market_value
        acumulado[1] += 1
    return PortfolioResult(
        total_value=total,
        total_proventos=1_234.56,
        positions_count=len(posicoes),
        weighted_var_12m=0.12,
        positions=posicoes,
        by_class=[
            ClassSummary(nome, valor, valor / total, int(quantidade))
            for nome, (valor, quantidade) in classes.items()
        ],
    )


def _proventos() -> ProventosResult:
    """12 ativos com proventos — o gráfico só pode mostrar 8."""
    por_ticker: List[Dict[str, Any]] = [
        {
            "ticker": f"T{indice:02d}",
            "label": f"T{indice:02d}",
            "product": "",
            "amount": float(1_200 - indice * 100),
            "share": 0.0,
            "count": 1,
            "last_paid_at": date(2025, 6, 1),
        }
        for indice in range(12)
    ]
    return ProventosResult(
        total=sum(item["amount"] for item in por_ticker),
        by_ticker=por_ticker,
        by_kind=[],
        by_month=[],
        top=[dict(item) for item in por_ticker[:10]],
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        monthly_average=100.0,
        yield_on_portfolio=0.01,
    )


def _fluxo() -> CashflowResult:
    """18 meses — o gráfico deve recortar os 12 últimos, em ordem."""
    meses = [
        MonthlyCashflow(
            month=date(2024 + (indice + 6) // 12, (indice + 6) % 12 + 1, 1),
            inflow=1_000.0 + indice,
            expenses=400.0 + indice,
            invest_contribution=300.0 + indice,
            savings=600.0,
            invested_balance=100_000.0 + indice,
            cash_balance=5_000.0,
            net_worth=105_000.0 + indice,
        )
        for indice in range(18)
    ]
    return CashflowResult(
        monthly=meses,
        daily_balance=[],
        total_inflow=18_000.0,
        total_expenses=7_200.0,
        total_contributions=5_400.0,
        savings_rate=0.6,
        final_invested=100_017.0,
    )


def _consolidado() -> ConsolidatedResult:
    categorias = [
        {"category": "Caixa", "label": "Caixa", "value": 30_000.0, "share": 0.3, "accounts": 2},
        {
            "category": "Investimentos BR",
            "label": "Investimentos BR",
            "value": 60_000.0,
            "share": 0.6,
            "accounts": 3,
        },
        {"category": "Cripto", "label": "Cripto", "value": 10_000.0, "share": 0.1, "accounts": 1},
    ]
    liquidez = [
        {"liquidity": "D+2", "label": "D+2", "value": 60_000.0, "share": 0.6, "accounts": 3},
        {
            "liquidity": "Imediata (D0)",
            "label": "Imediata (D0)",
            "value": 30_000.0,
            "share": 0.3,
            "accounts": 2,
        },
        {"liquidity": "Reserva", "label": "Reserva", "value": 10_000.0, "share": 0.1, "accounts": 1},
    ]
    return ConsolidatedResult(
        total=100_000.0,
        accounts=[],
        by_category=categorias,
        by_liquidity=liquidez,
        usd_brl=5.11,
        foreign_total_usd=0.0,
        foreign_total_brl=0.0,
        foreign=[],
        foreign_by_broker=[],
        foreign_result_usd=0.0,
    )


def _simulacao() -> SimulationResult:
    serie = [
        SimulationPoint(month=mes, balance=1_000.0 * (mes + 1), invested=900.0 * (mes + 1), interest=100.0 * (mes + 1))
        for mes in range(5)
    ]
    return SimulationResult(
        initial=1_000.0,
        monthly=100.0,
        annual_rate=0.11,
        monthly_rate=0.0087,
        months=4,
        final_balance=5_000.0,
        total_invested=4_500.0,
        total_interest=500.0,
        series=serie,
    )


def _projecao() -> ProjectionResult:
    serie = [
        ProjectionPoint(
            month=date(2026, mes, 1),
            contribution=120_000.0,
            value_income=2_500_000.0 + mes,
            income_income=18_700.0 + mes,
            value_growth=2_600_000.0 + mes,
            income_growth=18_700.0 + mes,
            value_selected=2_600_000.0 + mes,
            income_selected=18_700.0 + mes,
        )
        for mes in range(1, 4)
    ]
    return ProjectionResult(
        annual_return_income=0.1168905,
        annual_return_growth=0.1650425,
        scenario="Renda",
        series=serie,
        final_value=2_600_003.0,
        final_income=18_703.0,
    )


def _todos() -> List[ChartSeries]:
    return build_charts(
        _carteira(),
        _proventos(),
        None,
        _simulacao(),
        _projecao(),
        None,
        _fluxo(),
        _consolidado(),
    )


# --------------------------------------------------------------------------
# Conjunto e identidade dos gráficos
# --------------------------------------------------------------------------


def test_gera_os_nove_graficos_na_ordem_do_spec() -> None:
    assert [grafico.id for grafico in _todos()] == list(CHART_IDS)


def test_tipos_e_formatos_por_grafico() -> None:
    graficos = {grafico.id: grafico for grafico in _todos()}
    tipos = {
        "alocacao-classe": "donut",
        "top-posicoes": "bar",
        "melhores-piores": "bar",
        "maiores-proventos": "bar",
        "alocacao-patrimonio": "donut",
        "fluxo-vs-aporte": "bar",
        "liquidez": "bar",
        "evolucao-simulador": "line",
        "projecao-2030": "area",
    }
    for identificador, tipo in tipos.items():
        assert graficos[identificador].kind == tipo
        assert graficos[identificador].x_key == "label"
        assert graficos[identificador].series, identificador
        assert graficos[identificador].data, identificador

    assert graficos["melhores-piores"].value_format == "percent"
    for identificador in tipos:
        if identificador != "melhores-piores":
            assert graficos[identificador].value_format == "currency"


def test_titulos_em_portugues() -> None:
    titulos = {grafico.id: grafico.title for grafico in _todos()}
    assert titulos["alocacao-classe"] == "Distribuição por classe"
    assert titulos["top-posicoes"] == "Top 10 posições (R$)"
    assert titulos["melhores-piores"] == "Rentabilidade 12m — 5 melhores e 5 piores"
    assert titulos["maiores-proventos"] == "Maiores proventos no período (R$)"
    assert titulos["alocacao-patrimonio"] == "Alocação do Patrimônio"
    assert titulos["fluxo-vs-aporte"] == "Fluxo de Caixa × Aporte em Investimentos (12m)"
    assert titulos["liquidez"] == "Liquidez e Reserva de Oportunidade"
    assert titulos["evolucao-simulador"] == "Evolução do patrimônio — Simulador"
    assert titulos["projecao-2030"] == "Projeção do patrimônio até 2030"


def test_todas_as_chaves_das_series_existem_nos_pontos() -> None:
    for grafico in _todos():
        chaves = [serie["key"] for serie in grafico.series]
        for ponto in grafico.data:
            assert grafico.x_key in ponto
            for chave in chaves:
                assert chave in ponto, f"{grafico.id} sem {chave}"


def test_saida_serializavel_em_json() -> None:
    import json

    bruto = json.dumps([to_dict(grafico) for grafico in _todos()], ensure_ascii=False)
    assert '"alocacao-classe"' in bruto


# --------------------------------------------------------------------------
# Gráfico a gráfico
# --------------------------------------------------------------------------


def test_alocacao_classe_ordenada_e_com_participacao() -> None:
    grafico = chart_by_id(_todos(), "alocacao-classe")
    assert grafico is not None
    valores = [ponto["value"] for ponto in grafico.data]
    assert valores == sorted(valores, reverse=True)
    assert grafico.data[0]["label"] == "Ações"  # 33.000
    assert grafico.data[0]["value"] == pytest.approx(33_000.0, rel=REL)
    assert sum(ponto["share"] for ponto in grafico.data) == pytest.approx(1.0, rel=REL)


def test_top_posicoes_limita_a_dez_e_ordena_do_maior_para_o_menor() -> None:
    grafico = chart_by_id(_todos(), "top-posicoes")
    assert grafico is not None
    assert len(grafico.data) == 10
    valores = [ponto["value"] for ponto in grafico.data]
    assert valores == sorted(valores, reverse=True)
    assert grafico.data[0]["label"] == "AAAA1"
    assert grafico.data[-1]["label"] == "JJJJ1"  # 3.000; as duas menores ficaram de fora


def test_melhores_piores_traz_cinco_de_cada_ponta_do_melhor_para_o_pior() -> None:
    grafico = chart_by_id(_todos(), "melhores-piores")
    assert grafico is not None
    assert [ponto["label"] for ponto in grafico.data] == [
        "AAAA1",
        "BBBB1",
        "CCCC1",
        "DDDD1",
        "EEEE1",
        "FFFF1",
        "GGGG1",
        "HHHH1",
        "IIII1",
        "JJJJ1",
    ]
    variacoes = [ponto["value"] for ponto in grafico.data]
    assert variacoes == sorted(variacoes, reverse=True)
    assert [ponto["group"] for ponto in grafico.data[:5]] == ["Melhores"] * 5
    assert [ponto["group"] for ponto in grafico.data[5:]] == ["Piores"] * 5
    # Posições sem var_12m (Tesouro/RF) não entram no ranking.
    assert "KKKK1" not in {ponto["label"] for ponto in grafico.data}


def test_melhores_piores_nao_repete_ativo_quando_ha_menos_de_dez() -> None:
    posicoes = [
        _posicao("AAAA1", "Ações", 100.0, 0.30),
        _posicao("BBBB1", "Ações", 100.0, 0.20),
        _posicao("CCCC1", "Ações", 100.0, 0.10),
        _posicao("DDDD1", "Ações", 100.0, -0.10),
        _posicao("EEEE1", "Ações", 100.0, None),
    ]
    carteira = PortfolioResult(400.0, 0.0, 5, 0.0, posicoes, [])
    grafico = chart_by_id(build_charts(carteira), "melhores-piores")
    assert grafico is not None
    rotulos = [ponto["label"] for ponto in grafico.data]
    assert rotulos == ["AAAA1", "BBBB1", "CCCC1", "DDDD1"]
    assert len(rotulos) == len(set(rotulos))


def test_melhores_piores_sumindo_quando_ninguem_tem_var_12m() -> None:
    posicoes = [_posicao("AAAA1", "Ações", 100.0, None)]
    carteira = PortfolioResult(100.0, 0.0, 1, 0.0, posicoes, [])
    assert chart_by_id(build_charts(carteira), "melhores-piores") is None
    assert chart_by_id(build_charts(carteira), "top-posicoes") is not None


def test_maiores_proventos_limita_a_oito() -> None:
    grafico = chart_by_id(_todos(), "maiores-proventos")
    assert grafico is not None
    assert len(grafico.data) == 8
    valores = [ponto["value"] for ponto in grafico.data]
    assert valores == sorted(valores, reverse=True)
    assert grafico.data[0]["value"] == pytest.approx(1_200.0, rel=REL)


def test_fluxo_usa_os_doze_ultimos_meses_em_ordem_cronologica() -> None:
    grafico = chart_by_id(_todos(), "fluxo-vs-aporte")
    assert grafico is not None
    assert len(grafico.data) == 12
    meses = [ponto["month"] for ponto in grafico.data]
    assert meses == sorted(meses)
    assert grafico.data[0]["label"] == "jan/2025"  # 18 meses a partir de jul/2024
    assert grafico.data[-1]["label"] == "dez/2025"
    assert [serie["key"] for serie in grafico.series] == [
        "inflow",
        "expenses",
        "invest_contribution",
    ]
    assert [serie["label"] for serie in grafico.series] == [
        "Entradas",
        "Gastos",
        "Aporte em investimentos",
    ]


def test_liquidez_ordenada_do_maior_para_o_menor() -> None:
    grafico = chart_by_id(_todos(), "liquidez")
    assert grafico is not None
    assert [ponto["label"] for ponto in grafico.data] == ["D+2", "Imediata (D0)", "Reserva"]
    assert grafico.data[0]["value"] == pytest.approx(60_000.0, rel=REL)


def test_alocacao_patrimonio_ordenada_do_maior_para_o_menor() -> None:
    grafico = chart_by_id(_todos(), "alocacao-patrimonio")
    assert grafico is not None
    assert [ponto["label"] for ponto in grafico.data] == [
        "Investimentos BR",
        "Caixa",
        "Cripto",
    ]


def test_simulador_preserva_a_ordem_dos_meses() -> None:
    grafico = chart_by_id(_todos(), "evolucao-simulador")
    assert grafico is not None
    assert [ponto["month"] for ponto in grafico.data] == [0, 1, 2, 3, 4]
    assert grafico.data[0]["label"] == "Mês 0"
    assert grafico.data[4]["balance"] == pytest.approx(5_000.0, rel=REL)
    assert [serie["key"] for serie in grafico.series] == ["balance", "invested", "interest"]


def test_projecao_traz_renda_e_valorizacao_com_mes_iso() -> None:
    grafico = chart_by_id(_todos(), "projecao-2030")
    assert grafico is not None
    assert [serie["key"] for serie in grafico.series] == ["value_income", "value_growth"]
    assert grafico.data[0]["label"] == "jan/2026"
    assert grafico.data[0]["month"] == "2026-01-01"
    assert grafico.data[0]["value_income"] == pytest.approx(2_500_001.0, rel=REL)
    assert grafico.data[0]["value_growth"] == pytest.approx(2_600_001.0, rel=REL)
    assert "Renda" in grafico.note


# --------------------------------------------------------------------------
# Casos degenerados — nada pode levantar exceção
# --------------------------------------------------------------------------


def test_sem_nenhum_resultado_devolve_lista_vazia() -> None:
    assert build_charts(None, None, None, None, None, None, None, None) == []
    assert build_charts() == []


def test_apenas_carteira_gera_apenas_os_graficos_da_carteira() -> None:
    ids = [grafico.id for grafico in build_charts(_carteira())]
    assert ids == ["alocacao-classe", "top-posicoes", "melhores-piores"]


def test_resultados_vazios_pulam_os_graficos_correspondentes() -> None:
    carteira_vazia = PortfolioResult(0.0, 0.0, 0, 0.0, [], [])
    proventos_vazios = ProventosResult(0.0, [], [], [], [], None, None, 0.0, 0.0)
    fluxo_vazio = CashflowResult([], [], 0.0, 0.0, 0.0, 0.0, 0.0)
    consolidado_vazio = ConsolidatedResult(0.0, [], [], [], 0.0, 0.0, 0.0, [], [], 0.0)
    simulacao_vazia = SimulationResult(0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, [])
    projecao_vazia = ProjectionResult(0.0, 0.0, "Renda", [], 0.0, 0.0)
    assert (
        build_charts(
            carteira_vazia,
            proventos_vazios,
            None,
            simulacao_vazia,
            projecao_vazia,
            None,
            fluxo_vazio,
            consolidado_vazio,
        )
        == []
    )


def test_tolera_dicts_no_lugar_de_dataclasses_e_divisao_por_zero() -> None:
    carteira = SimpleNamespace(
        total_value=0.0,
        positions=[],
        by_class=[
            {"asset_class": "Ações", "value": 0.0},
            {"asset_class": "", "value": 0.0},
        ],
    )
    grafico = chart_by_id(build_charts(carteira), "alocacao-classe")  # type: ignore[arg-type]
    assert grafico is not None
    assert [ponto["share"] for ponto in grafico.data] == [0.0, 0.0]
    assert grafico.data[1]["label"] == "Outros"


def test_campos_none_ou_invalidos_viram_zero() -> None:
    posicoes = [
        SimpleNamespace(
            ticker="",
            name="Fundo sem código",
            asset_class=None,
            market_value=None,
            portfolio_weight=None,
            var_12m=float("nan"),
        )
    ]
    carteira = SimpleNamespace(total_value=None, positions=posicoes, by_class=[])
    graficos = build_charts(carteira)  # type: ignore[arg-type]
    assert [grafico.id for grafico in graficos] == ["top-posicoes"]
    ponto = graficos[0].data[0]
    assert ponto["label"] == "Fundo sem código"
    assert ponto["value"] == 0.0
    assert ponto["asset_class"] == "Outros"


def test_proventos_sem_top_usa_by_ticker() -> None:
    proventos = ProventosResult(
        total=100.0,
        by_ticker=[{"ticker": "PETR4", "amount": 100.0}],
        by_kind=[],
        by_month=[],
        top=[],
        period_start=None,
        period_end=None,
        monthly_average=0.0,
        yield_on_portfolio=0.0,
    )
    grafico = chart_by_id(build_charts(None, proventos), "maiores-proventos")
    assert grafico is not None
    assert grafico.data == [{"label": "PETR4", "value": 100.0, "share": 0.0, "count": 0}]


def test_chart_by_id_com_lista_vazia_ou_id_inexistente() -> None:
    assert chart_by_id([], "alocacao-classe") is None
    assert chart_by_id(_todos(), "nao-existe") is None
