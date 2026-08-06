"""Testes de ``engine.calc.proventos`` — fixtures sintéticas."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc.proventos import TOP_N, compute_proventos  # noqa: E402
from engine.models import (  # noqa: E402
    PortfolioResult,
    Provento,
    WorkbookInputs,
    to_dict,
)


def _lancamento(
    dia: Optional[date],
    tipo: str,
    ticker: str,
    valor: float,
    produto: str = "",
) -> Provento:
    return Provento(
        paid_at=dia,
        kind=tipo,
        ticker=ticker,
        product=produto or (f"{ticker} - FUNDO SINTETICO" if ticker else ""),
        amount=valor,
    )


def _inputs(lancamentos: List[Provento]) -> WorkbookInputs:
    return WorkbookInputs(proventos=lancamentos)


def _carteira(valor: float) -> PortfolioResult:
    return PortfolioResult(
        total_value=valor,
        total_proventos=0.0,
        positions_count=0,
        weighted_var_12m=0.0,
        positions=[],
        by_class=[],
    )


# Carteira sintética: 2 meses, tipos com a grafia bagunçada da B3 e um papel
# genérico (CRX) que só se identifica pelo código dentro do produto.
BASE: List[Provento] = [
    _lancamento(date(2026, 3, 10), "Rendimento", "AAAA11", 100.0),
    _lancamento(date(2026, 3, 10), "Rendimento", "BBBB11", 40.0),
    _lancamento(
        date(2026, 3, 15),
        "AMORTIZACAO PROGRAMADA",
        "CRX",
        500.0,
        "CRX - CRX000001Z - SECURITIZADORA SINTETICA",
    ),
    _lancamento(
        date(2026, 3, 15),
        "PAGAMENTO DE JUROS",
        "CRX",
        25.0,
        "CRX - CRX000001Z - SECURITIZADORA SINTETICA",
    ),
    _lancamento(date(2026, 3, 20), "Juros Sobre Capital Próprio", "CCC3", 30.0),
    _lancamento(date(2026, 3, 20), "Dividendo", "CCC3", 20.0),
    _lancamento(date(2026, 4, 5), "Rendimento", "AAAA11", 110.0),
    _lancamento(date(2026, 4, 5), "Amortização", "BBBB11", 15.0),
]

TOTAL_BASE = 840.0


def test_total_e_periodo() -> None:
    resultado = compute_proventos(_inputs(BASE))

    assert resultado.total == pytest.approx(TOTAL_BASE, rel=1e-9)
    assert resultado.period_start == date(2026, 3, 10)
    assert resultado.period_end == date(2026, 4, 5)
    # 840 em 2 meses distintos.
    assert resultado.monthly_average == pytest.approx(420.0, rel=1e-9)


def test_by_ticker_ordenado_com_rotulo_e_share() -> None:
    resultado = compute_proventos(_inputs(BASE))

    assert [item["ticker"] for item in resultado.by_ticker] == [
        "CRX",
        "AAAA11",
        "BBBB11",
        "CCC3",
    ]
    assert [item["amount"] for item in resultado.by_ticker] == [525.0, 210.0, 55.0, 50.0]
    crx = resultado.by_ticker[0]
    # Papel genérico ganha o código do produto no rótulo; ticker real fica como está.
    assert crx["label"] == "CRX - CRX000001Z"
    assert crx["count"] == 2
    assert crx["last_paid_at"] == date(2026, 3, 15)
    assert crx["share"] == pytest.approx(525.0 / TOTAL_BASE, rel=1e-9)
    assert resultado.by_ticker[1]["label"] == "AAAA11"
    assert sum(item["share"] for item in resultado.by_ticker) == pytest.approx(1.0, rel=1e-9)


def test_by_kind_canoniza_grafias_da_b3() -> None:
    resultado = compute_proventos(_inputs(BASE))
    por_tipo = {item["kind"]: item for item in resultado.by_kind}

    assert set(por_tipo) == {"Amortização", "Rendimento", "JCP", "Juros", "Dividendo"}
    # AMORTIZACAO PROGRAMADA + Amortização caem no mesmo tipo.
    assert por_tipo["Amortização"]["amount"] == pytest.approx(515.0, rel=1e-9)
    assert por_tipo["Amortização"]["count"] == 2
    assert por_tipo["Amortização"]["raw_kinds"] == ["AMORTIZACAO PROGRAMADA", "Amortização"]
    assert por_tipo["JCP"]["amount"] == pytest.approx(30.0, rel=1e-9)
    assert por_tipo["Juros"]["amount"] == pytest.approx(25.0, rel=1e-9)
    assert por_tipo["Rendimento"]["amount"] == pytest.approx(250.0, rel=1e-9)
    # Ordem decrescente por valor.
    valores = [item["amount"] for item in resultado.by_kind]
    assert valores == sorted(valores, reverse=True)
    assert sum(valores) == pytest.approx(TOTAL_BASE, rel=1e-9)


def test_by_month_cronologico() -> None:
    resultado = compute_proventos(_inputs(BASE))

    assert [item["month"] for item in resultado.by_month] == ["2026-03", "2026-04"]
    assert [item["label"] for item in resultado.by_month] == ["mar/2026", "abr/2026"]
    assert resultado.by_month[0]["amount"] == pytest.approx(715.0, rel=1e-9)
    assert resultado.by_month[1]["amount"] == pytest.approx(125.0, rel=1e-9)
    assert resultado.by_month[0]["count"] == 6
    assert resultado.by_month[1]["share"] == pytest.approx(125.0 / TOTAL_BASE, rel=1e-9)


def test_top_limita_a_dez_maiores() -> None:
    muitos = [
        _lancamento(date(2026, 5, 1), "Rendimento", f"T{i:02d}11", float(i))
        for i in range(1, 16)
    ]
    resultado = compute_proventos(_inputs(muitos))

    assert len(resultado.by_ticker) == 15
    assert len(resultado.top) == TOP_N
    assert [item["ticker"] for item in resultado.top] == [
        f"T{i:02d}11" for i in range(15, 5, -1)
    ]
    assert resultado.top[0]["amount"] == pytest.approx(15.0, rel=1e-9)


def test_yield_on_portfolio() -> None:
    resultado = compute_proventos(_inputs(BASE), _carteira(84_000.0))
    assert resultado.yield_on_portfolio == pytest.approx(0.01, rel=1e-9)

    # Sem carteira ou com carteira zerada: 0.0, nunca ZeroDivisionError.
    assert compute_proventos(_inputs(BASE)).yield_on_portfolio == 0.0
    assert compute_proventos(_inputs(BASE), _carteira(0.0)).yield_on_portfolio == 0.0


def test_soma_preserva_a_ordem_das_linhas() -> None:
    """A soma acumula na ordem da planilha (mesmo resultado binário do Excel)."""
    lancamentos = [
        _lancamento(date(2026, 1, 5), "Rendimento", "AAAA11", 0.1),
        _lancamento(date(2026, 1, 6), "Rendimento", "AAAA11", 0.2),
    ]
    resultado = compute_proventos(_inputs(lancamentos))

    assert resultado.total == 0.1 + 0.2  # 0.30000000000000004, sem arredondar
    assert resultado.by_ticker[0]["amount"] == 0.1 + 0.2


def test_lancamentos_sem_data() -> None:
    lancamentos = [
        _lancamento(None, "Rendimento", "AAAA11", 100.0),
        _lancamento(date(2026, 2, 10), "Rendimento", "BBBB11", 300.0),
    ]
    resultado = compute_proventos(_inputs(lancamentos))

    assert resultado.total == pytest.approx(400.0, rel=1e-9)
    assert resultado.period_start == date(2026, 2, 10)
    assert resultado.period_end == date(2026, 2, 10)
    # Só o lançamento datado entra no mês; o outro continua no total.
    assert [item["month"] for item in resultado.by_month] == ["2026-02"]
    assert resultado.by_month[0]["amount"] == pytest.approx(300.0, rel=1e-9)
    assert resultado.monthly_average == pytest.approx(400.0, rel=1e-9)
    assert resultado.by_ticker[0]["last_paid_at"] == date(2026, 2, 10)
    assert resultado.by_ticker[1]["last_paid_at"] is None


def test_tudo_sem_data() -> None:
    resultado = compute_proventos(_inputs([_lancamento(None, "", "AAAA11", 50.0)]))

    assert resultado.total == pytest.approx(50.0, rel=1e-9)
    assert resultado.period_start is None
    assert resultado.period_end is None
    assert resultado.by_month == []
    assert resultado.monthly_average == 0.0
    assert resultado.by_kind[0]["kind"] == "Outros"


def test_entradas_degeneradas() -> None:
    vazio = compute_proventos(WorkbookInputs())

    assert vazio.total == 0.0
    assert vazio.by_ticker == []
    assert vazio.by_kind == []
    assert vazio.by_month == []
    assert vazio.top == []
    assert vazio.period_start is None and vazio.period_end is None
    assert vazio.monthly_average == 0.0
    assert vazio.yield_on_portfolio == 0.0

    # Valores zerados: share não divide por zero.
    zerado = compute_proventos(
        _inputs([_lancamento(date(2026, 1, 1), "Rendimento", "AAAA11", 0.0)]),
        _carteira(1000.0),
    )
    assert zerado.total == 0.0
    assert zerado.by_ticker[0]["share"] == 0.0
    assert zerado.by_month[0]["share"] == 0.0
    assert zerado.yield_on_portfolio == 0.0

    # Lançamento sem ticker e sem produto não quebra o agrupamento.
    sem_ticker = compute_proventos(_inputs([_lancamento(date(2026, 1, 1), "", "", 10.0)]))
    assert sem_ticker.by_ticker[0]["ticker"] == "(sem ticker)"
    assert sem_ticker.by_ticker[0]["label"] == "(sem ticker)"


def test_resultado_serializa_em_json() -> None:
    import json

    resultado = compute_proventos(_inputs(BASE), _carteira(84_000.0))
    payload = json.loads(json.dumps(to_dict(resultado)))

    assert payload["period_start"] == "2026-03-10"
    assert payload["top"][0]["last_paid_at"] == "2026-03-15"
    assert payload["by_month"][0]["month"] == "2026-03"
