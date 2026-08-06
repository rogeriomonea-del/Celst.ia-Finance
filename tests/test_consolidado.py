"""Testes de ``engine.calc.consolidado`` — fixtures sintéticas, nunca dados reais."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc.consolidado import compute_consolidated  # noqa: E402
from engine.models import (  # noqa: E402
    ConsolidatedAccount,
    ForeignPosition,
    WorkbookInputs,
)

REL = 1e-9


# --------------------------------------------------------------------------
# Fixtures sintéticas
# --------------------------------------------------------------------------


def _contas() -> List[ConsolidatedAccount]:
    """Seis contas somando R$ 100.000 — pesos conferíveis à mão."""
    return [
        ConsolidatedAccount("Corretora Alfa", "Investimentos BR", "D+2", 60_000.0),
        ConsolidatedAccount("Banco Beta", "Caixa", "Imediata (D0)", 20_000.0),
        ConsolidatedAccount("Banco Gama", "Caixa", "Imediata (D0)", 5_000.0),
        ConsolidatedAccount("Global Delta", "Internacional", "D+2 (câmbio)", 10_000.0),
        ConsolidatedAccount("Cripto Épsilon", "Cripto", "Imediata (D0)", 4_000.0),
        ConsolidatedAccount("Reserva Zeta", "Reserva", "Reserva", 1_000.0),
    ]


def _exterior() -> List[ForeignPosition]:
    """Quatro posições somando US$ 1.505, incluindo uma de custo zero."""
    return [
        ForeignPosition("Alfa Global", "VT", "ETF mundo", 100.0, 10.0, 800.0),  # 1.000
        ForeignPosition("Alfa Global", "BND", "ETF renda fixa", 50.0, 4.0, 250.0),  # 200
        ForeignPosition("Beta Invest", "MSFT", "Microsoft", 2.0, 150.0, 200.0),  # 300
        ForeignPosition("Beta Invest", "AIRD", "Token bonificado", 10.0, 0.5, 0.0),  # 5
    ]


CAMBIO = 5.0
TOTAL = 100_000.0
TOTAL_USD = 1_505.0


def _inputs() -> WorkbookInputs:
    return WorkbookInputs(accounts=_contas(), foreign=_exterior(), usd_brl=CAMBIO)


# --------------------------------------------------------------------------
# Total e contas
# --------------------------------------------------------------------------


def test_total_soma_todas_as_contas() -> None:
    resultado = compute_consolidated(_inputs())

    assert resultado.total == pytest.approx(TOTAL, rel=REL)
    assert len(resultado.accounts) == 6


def test_contas_trazem_campos_e_share() -> None:
    resultado = compute_consolidated(_inputs())

    por_nome = {c["institution"]: c for c in resultado.accounts}
    alfa = por_nome["Corretora Alfa"]
    assert alfa["category"] == "Investimentos BR"
    assert alfa["liquidity"] == "D+2"
    assert alfa["value"] == pytest.approx(60_000.0, rel=REL)
    assert alfa["share"] == pytest.approx(0.60, rel=REL)
    assert por_nome["Reserva Zeta"]["share"] == pytest.approx(0.01, rel=REL)
    assert sum(c["share"] for c in resultado.accounts) == pytest.approx(1.0, rel=REL)


def test_contas_saem_ordenadas_por_valor_desc() -> None:
    resultado = compute_consolidated(_inputs())

    valores = [c["value"] for c in resultado.accounts]
    assert valores == sorted(valores, reverse=True)
    assert resultado.accounts[0]["institution"] == "Corretora Alfa"


# --------------------------------------------------------------------------
# Agregação por categoria e por liquidez
# --------------------------------------------------------------------------


def test_por_categoria_agrega_ordena_e_calcula_share() -> None:
    resultado = compute_consolidated(_inputs())

    assert [c["category"] for c in resultado.by_category] == [
        "Investimentos BR",
        "Caixa",
        "Internacional",
        "Cripto",
        "Reserva",
    ]
    por_categoria = {c["category"]: c for c in resultado.by_category}
    assert por_categoria["Caixa"]["value"] == pytest.approx(25_000.0, rel=REL)  # 20k + 5k
    assert por_categoria["Caixa"]["accounts"] == 2
    assert por_categoria["Caixa"]["share"] == pytest.approx(0.25, rel=REL)
    assert por_categoria["Cripto"]["share"] == pytest.approx(0.04, rel=REL)
    assert sum(c["share"] for c in resultado.by_category) == pytest.approx(1.0, rel=REL)
    # ``label`` genérico acompanha o campo específico (usado pelos gráficos).
    assert all(c["label"] == c["category"] for c in resultado.by_category)


def test_por_liquidez_agrega_ordena_e_calcula_share() -> None:
    resultado = compute_consolidated(_inputs())

    assert [c["liquidity"] for c in resultado.by_liquidity] == [
        "D+2",
        "Imediata (D0)",
        "D+2 (câmbio)",
        "Reserva",
    ]
    por_liquidez = {c["liquidity"]: c for c in resultado.by_liquidity}
    assert por_liquidez["Imediata (D0)"]["value"] == pytest.approx(29_000.0, rel=REL)
    assert por_liquidez["Imediata (D0)"]["accounts"] == 3
    assert por_liquidez["D+2"]["share"] == pytest.approx(0.60, rel=REL)
    assert all(c["label"] == c["liquidity"] for c in resultado.by_liquidity)


def test_acentuacao_nao_separa_grupos_mas_d2_cambio_continua_distinto() -> None:
    """'D+2 (câmbio)' e 'D+2 (cambio)' são o mesmo grupo; 'D+2' não."""
    inputs = WorkbookInputs(
        accounts=[
            ConsolidatedAccount("A", "Internacional", "D+2 (câmbio)", 1_000.0),
            ConsolidatedAccount("B", "internacional", "D+2 (cambio)", 500.0),
            ConsolidatedAccount("C", "Investimentos BR", "D+2", 2_000.0),
        ]
    )
    resultado = compute_consolidated(inputs)

    por_liquidez = {c["liquidity"]: c["value"] for c in resultado.by_liquidity}
    assert por_liquidez["D+2 (câmbio)"] == pytest.approx(1_500.0, rel=REL)
    assert por_liquidez["D+2"] == pytest.approx(2_000.0, rel=REL)
    # O rótulo exibido é o da primeira ocorrência (o acentuado da planilha).
    assert [c["category"] for c in resultado.by_category] == [
        "Investimentos BR",
        "Internacional",
    ]


def test_categoria_e_liquidez_vazias_ganham_rotulo_padrao() -> None:
    inputs = WorkbookInputs(accounts=[ConsolidatedAccount("Conta solta", "", "", 700.0)])
    resultado = compute_consolidated(inputs)

    assert resultado.accounts[0]["category"] == "Outros"
    assert resultado.accounts[0]["liquidity"] == "Não informada"
    assert resultado.by_category[0]["category"] == "Outros"
    assert resultado.by_liquidity[0]["liquidity"] == "Não informada"


# --------------------------------------------------------------------------
# Exterior
# --------------------------------------------------------------------------


def test_exterior_totais_em_dolar_e_em_real() -> None:
    resultado = compute_consolidated(_inputs())

    assert resultado.usd_brl == pytest.approx(CAMBIO, rel=REL)
    assert resultado.foreign_total_usd == pytest.approx(TOTAL_USD, rel=REL)
    assert resultado.foreign_total_brl == pytest.approx(TOTAL_USD * CAMBIO, rel=REL)
    # 200 - 50 + 100 + 5
    assert resultado.foreign_result_usd == pytest.approx(255.0, rel=REL)


def test_exterior_por_posicao() -> None:
    resultado = compute_consolidated(_inputs())

    por_ticker = {f["ticker"]: f for f in resultado.foreign}
    vt = por_ticker["VT"]
    assert vt["broker"] == "Alfa Global"
    assert vt["description"] == "ETF mundo"
    assert vt["quantity"] == pytest.approx(100.0, rel=REL)
    assert vt["price_usd"] == pytest.approx(10.0, rel=REL)
    assert vt["value_usd"] == pytest.approx(1_000.0, rel=REL)
    assert vt["cost_usd"] == pytest.approx(800.0, rel=REL)
    assert vt["result_usd"] == pytest.approx(200.0, rel=REL)
    assert vt["result_percent"] == pytest.approx(0.25, rel=REL)
    assert vt["value_brl"] == pytest.approx(5_000.0, rel=REL)
    assert vt["share"] == pytest.approx(1_000.0 / TOTAL_USD, rel=REL)

    assert por_ticker["BND"]["result_usd"] == pytest.approx(-50.0, rel=REL)
    assert por_ticker["BND"]["result_percent"] == pytest.approx(-0.2, rel=REL)
    assert sum(f["share"] for f in resultado.foreign) == pytest.approx(1.0, rel=REL)
    valores = [f["value_usd"] for f in resultado.foreign]
    assert valores == sorted(valores, reverse=True)


def test_exterior_custo_zero_nao_divide_por_zero() -> None:
    resultado = compute_consolidated(_inputs())

    airdrop = next(f for f in resultado.foreign if f["ticker"] == "AIRD")
    assert airdrop["cost_usd"] == 0.0
    assert airdrop["result_usd"] == pytest.approx(5.0, rel=REL)
    assert airdrop["result_percent"] == 0.0


def test_subtotais_por_corretora() -> None:
    resultado = compute_consolidated(_inputs())

    por_corretora = {b["broker"]: b for b in resultado.foreign_by_broker}
    assert [b["broker"] for b in resultado.foreign_by_broker] == ["Alfa Global", "Beta Invest"]

    alfa = por_corretora["Alfa Global"]
    assert alfa["value_usd"] == pytest.approx(1_200.0, rel=REL)
    assert alfa["value_brl"] == pytest.approx(6_000.0, rel=REL)
    assert alfa["cost_usd"] == pytest.approx(1_050.0, rel=REL)
    assert alfa["result_usd"] == pytest.approx(150.0, rel=REL)
    assert alfa["result_percent"] == pytest.approx(150.0 / 1_050.0, rel=REL)
    assert alfa["positions"] == 2
    assert alfa["share"] == pytest.approx(1_200.0 / TOTAL_USD, rel=REL)

    beta = por_corretora["Beta Invest"]
    assert beta["value_usd"] == pytest.approx(305.0, rel=REL)
    assert beta["result_percent"] == pytest.approx(105.0 / 200.0, rel=REL)

    soma = sum(b["value_usd"] for b in resultado.foreign_by_broker)
    assert soma == pytest.approx(resultado.foreign_total_usd, rel=REL)


def test_corretora_vazia_ganha_rotulo_padrao() -> None:
    inputs = WorkbookInputs(
        foreign=[ForeignPosition("", "AAPL", "Apple", 1.0, 100.0, 90.0)], usd_brl=5.0
    )
    resultado = compute_consolidated(inputs)

    assert resultado.foreign[0]["broker"] == "Exterior"
    assert resultado.foreign_by_broker[0]["broker"] == "Exterior"


# --------------------------------------------------------------------------
# Câmbio ausente
# --------------------------------------------------------------------------


def test_sem_cambio_os_valores_em_real_ficam_zerados_sem_quebrar() -> None:
    inputs = WorkbookInputs(accounts=_contas(), foreign=_exterior())  # usd_brl = 0.0
    resultado = compute_consolidated(inputs)

    assert resultado.usd_brl == 0.0
    assert resultado.foreign_total_usd == pytest.approx(TOTAL_USD, rel=REL)
    assert resultado.foreign_total_brl == 0.0
    assert all(f["value_brl"] == 0.0 for f in resultado.foreign)
    assert all(b["value_brl"] == 0.0 for b in resultado.foreign_by_broker)
    # O bloco em US$ e o total em R$ das contas seguem íntegros.
    assert resultado.foreign_result_usd == pytest.approx(255.0, rel=REL)
    assert resultado.total == pytest.approx(TOTAL, rel=REL)


def test_cambio_negativo_e_tratado_como_ausente() -> None:
    inputs = WorkbookInputs(foreign=_exterior(), usd_brl=-5.11)
    resultado = compute_consolidated(inputs)

    assert resultado.usd_brl == 0.0
    assert resultado.foreign_total_brl == 0.0


# --------------------------------------------------------------------------
# Casos degenerados
# --------------------------------------------------------------------------


def test_planilha_vazia_devolve_zeros_e_listas_vazias() -> None:
    resultado = compute_consolidated(WorkbookInputs())

    assert resultado.total == 0.0
    assert resultado.accounts == []
    assert resultado.by_category == []
    assert resultado.by_liquidity == []
    assert resultado.foreign == []
    assert resultado.foreign_by_broker == []
    assert resultado.foreign_total_usd == 0.0
    assert resultado.foreign_total_brl == 0.0
    assert resultado.foreign_result_usd == 0.0
    assert resultado.usd_brl == 0.0


def test_listas_nulas_e_inputs_nulo_nao_quebram() -> None:
    inputs = WorkbookInputs()
    inputs.accounts = None  # type: ignore[assignment]
    inputs.foreign = None  # type: ignore[assignment]
    resultado = compute_consolidated(inputs)
    assert resultado.total == 0.0
    assert resultado.foreign == []

    vazio = compute_consolidated(None)  # type: ignore[arg-type]
    assert vazio.total == 0.0
    assert vazio.accounts == []


def test_itens_nulos_na_lista_sao_ignorados() -> None:
    inputs = WorkbookInputs(
        accounts=[ConsolidatedAccount("A", "Caixa", "Imediata (D0)", 500.0), None],  # type: ignore[list-item]
        foreign=[None, ForeignPosition("X", "AAA", "Ativo", 2.0, 3.0, 5.0)],  # type: ignore[list-item]
        usd_brl=2.0,
    )
    resultado = compute_consolidated(inputs)

    assert len(resultado.accounts) == 1
    assert resultado.total == pytest.approx(500.0, rel=REL)
    assert len(resultado.foreign) == 1
    assert resultado.foreign_total_usd == pytest.approx(6.0, rel=REL)


def test_total_zero_zera_os_shares_sem_dividir_por_zero() -> None:
    inputs = WorkbookInputs(
        accounts=[
            ConsolidatedAccount("A", "Caixa", "Imediata (D0)", 0.0),
            ConsolidatedAccount("B", "Reserva", "Reserva", 0.0),
        ]
    )
    resultado = compute_consolidated(inputs)

    assert resultado.total == 0.0
    assert all(c["share"] == 0.0 for c in resultado.accounts)
    assert all(c["share"] == 0.0 for c in resultado.by_category)
    assert all(c["share"] == 0.0 for c in resultado.by_liquidity)


def test_valores_nulos_nan_e_infinitos_viram_zero() -> None:
    inputs = WorkbookInputs(
        accounts=[
            ConsolidatedAccount("Nula", "Caixa", "Imediata (D0)", None),  # type: ignore[arg-type]
            ConsolidatedAccount("NaN", "Caixa", "Imediata (D0)", float("nan")),
            ConsolidatedAccount("Inf", "Caixa", "Imediata (D0)", float("inf")),
            ConsolidatedAccount("Boa", "Caixa", "Imediata (D0)", 300.0),
        ],
        foreign=[
            ForeignPosition("X", "AAA", "Sem qtde", None, 10.0, None),  # type: ignore[arg-type]
            ForeignPosition("X", "BBB", "Preço texto", 5.0, "n/d", 10.0),  # type: ignore[arg-type]
        ],
        usd_brl=5.0,
    )
    resultado = compute_consolidated(inputs)

    assert resultado.total == pytest.approx(300.0, rel=REL)
    assert resultado.foreign_total_usd == 0.0
    assert resultado.foreign_total_brl == 0.0
    assert resultado.foreign_result_usd == pytest.approx(-10.0, rel=REL)
    assert all(f["share"] == 0.0 for f in resultado.foreign)


def test_conta_negativa_nao_quebra_o_total_nem_os_shares() -> None:
    """Uma conta no vermelho (cheque especial) só reduz o total."""
    inputs = WorkbookInputs(
        accounts=[
            ConsolidatedAccount("Investimentos", "Investimentos BR", "D+2", 1_000.0),
            ConsolidatedAccount("Cheque especial", "Caixa", "Imediata (D0)", -200.0),
        ]
    )
    resultado = compute_consolidated(inputs)

    assert resultado.total == pytest.approx(800.0, rel=REL)
    por_categoria = {c["category"]: c for c in resultado.by_category}
    assert por_categoria["Caixa"]["value"] == pytest.approx(-200.0, rel=REL)
    assert por_categoria["Caixa"]["share"] == pytest.approx(-0.25, rel=REL)
    assert [c["category"] for c in resultado.by_category] == ["Investimentos BR", "Caixa"]
