"""Testes de ``engine.calc.rebalance`` — fixtures sintéticas, nunca dados reais."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc.rebalance import compute_rebalance  # noqa: E402
from engine.models import (  # noqa: E402
    Assumptions,
    ClassSummary,
    PortfolioResult,
    RebalanceTarget,
    WorkbookInputs,
)

REL = 1e-9


# --------------------------------------------------------------------------
# Fixtures sintéticas
# --------------------------------------------------------------------------


def _carteira(classes: Sequence[Tuple[str, float]], total: Optional[float] = None) -> PortfolioResult:
    """Carteira mínima: o rebalanceamento só lê ``total_value`` e ``by_class``."""
    soma = sum(valor for _, valor in classes) if total is None else total
    return PortfolioResult(
        total_value=soma,
        total_proventos=0.0,
        positions_count=len(classes),
        weighted_var_12m=0.0,
        positions=[],
        by_class=[
            ClassSummary(
                asset_class=nome,
                value=valor,
                weight=(valor / soma if soma else 0.0),
                positions=1,
            )
            for nome, valor in classes
        ],
    )


def _entradas(
    alvos: Sequence[Tuple[str, float]],
    aporte: float = 0.0,
    tolerancia: float = 500.0,
) -> WorkbookInputs:
    return WorkbookInputs(
        rebalance_targets=[RebalanceTarget(nome, alvo) for nome, alvo in alvos],
        assumptions=Assumptions(monthly_contribution=aporte, order_tolerance=tolerancia),
    )


# Carteira de 1.000.000 que reproduz, com números redondos, exatamente os pesos
# por classe da planilha de referência (mesmos 6 primeiros decimais).
CLASSES_REFERENCIA: List[Tuple[str, float]] = [
    ("Ações", 322_930.0),  # 32,2930%
    ("FIIs", 353_938.0),  # 35,3938%
    ("ETFs", 32_464.0),  # 3,2464%
    ("BDRs", 7_904.0),  # 0,7904%
    ("Fundos listados", 24_143.0),  # 2,4143%
    ("Renda Fixa privada", 135_725.0),  # 13,5725%
    ("Tesouro Direto", 122_896.0),  # 12,2896%
]

ALVOS_REFERENCIA: List[Tuple[str, float]] = [
    ("Ações", 0.32),
    ("FIIs", 0.35),
    ("ETFs", 0.03),
    ("BDRs", 0.01),
    ("Fundos listados", 0.03),
    ("Renda Fixa privada", 0.14),
    ("Tesouro Direto", 0.12),
]


def _acoes(resultado: Any) -> List[str]:
    return [linha.action for linha in resultado.rows]


# --------------------------------------------------------------------------
# Paridade com a planilha
# --------------------------------------------------------------------------


def test_ordem_das_linhas_segue_a_tabela_de_alvos() -> None:
    resultado = compute_rebalance(_entradas(ALVOS_REFERENCIA), _carteira(CLASSES_REFERENCIA))
    assert [linha.asset_class for linha in resultado.rows] == [
        "Ações",
        "FIIs",
        "ETFs",
        "BDRs",
        "Fundos listados",
        "Renda Fixa privada",
        "Tesouro Direto",
    ]


def test_paridade_das_acoes_por_classe() -> None:
    """Veredito de cada classe com as bandas de 20% relativas.

    A planilha marca ETFs como VENDER, mas isso é defeito dela: a célula
    ``Rebalanceamento!G21`` ("Banda máx." dos ETFs) está vazia — todas as outras
    linhas têm ``=E*1.2`` — e o Excel compara ``0,032464 > 0`` (célula vazia lida
    como zero), devolvendo VENDER sempre. Com a banda calculada (2,4%–3,6%) o
    peso de 3,2464% está dentro, então o correto é OK.
    """
    resultado = compute_rebalance(_entradas(ALVOS_REFERENCIA), _carteira(CLASSES_REFERENCIA))
    assert _acoes(resultado) == ["OK", "OK", "OK", "COMPRAR", "OK", "OK", "OK"]

    etfs = resultado.rows[2]
    assert etfs.band_min == pytest.approx(0.024, rel=REL)
    assert etfs.band_max == pytest.approx(0.036, rel=REL)
    assert etfs.band_min < etfs.current_weight < etfs.band_max


def test_valores_pesos_e_soma_dos_alvos() -> None:
    resultado = compute_rebalance(_entradas(ALVOS_REFERENCIA), _carteira(CLASSES_REFERENCIA))
    assert resultado.total_value == pytest.approx(1_000_000.0, rel=REL)
    assert resultado.targets_sum == pytest.approx(1.0, rel=REL)
    assert resultado.band_relative == pytest.approx(0.20, rel=REL)

    acoes = resultado.rows[0]
    assert acoes.current_value == pytest.approx(322_930.0, rel=REL)
    assert acoes.current_weight == pytest.approx(0.322930, rel=REL)


def test_bdrs_compra_por_romper_a_banda_minima() -> None:
    resultado = compute_rebalance(_entradas(ALVOS_REFERENCIA), _carteira(CLASSES_REFERENCIA))
    bdrs = resultado.rows[3]
    assert bdrs.action == "COMPRAR"
    assert bdrs.current_weight == pytest.approx(0.007904, rel=REL)
    assert bdrs.band_min == pytest.approx(0.008, rel=REL)
    # Desvio negativo = abaixo do alvo; valor positivo = comprar.
    assert bdrs.deviation == pytest.approx(0.007904 - 0.01, rel=REL)
    assert bdrs.amount == pytest.approx((0.01 - 0.007904) * 1_000_000.0, rel=REL)


# --------------------------------------------------------------------------
# Bandas, desvio e valor do ajuste
# --------------------------------------------------------------------------


def test_bandas_sao_relativas_ao_alvo() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.60), ("FIIs", 0.40)]),
        _carteira([("Ações", 60_000.0), ("FIIs", 40_000.0)]),
    )
    assert resultado.rows[0].band_min == pytest.approx(0.48, rel=REL)
    assert resultado.rows[0].band_max == pytest.approx(0.72, rel=REL)
    assert resultado.rows[1].band_min == pytest.approx(0.32, rel=REL)
    assert resultado.rows[1].band_max == pytest.approx(0.48, rel=REL)


def test_valor_do_ajuste_positivo_compra_negativo_vende() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.50), ("FIIs", 0.50)]),
        _carteira([("Ações", 20_000.0), ("FIIs", 80_000.0)]),
    )
    compra, venda = resultado.rows
    assert compra.action == "COMPRAR"
    assert compra.deviation == pytest.approx(-0.30, rel=REL)
    assert compra.amount == pytest.approx(30_000.0, rel=REL)
    assert venda.action == "VENDER"
    assert venda.deviation == pytest.approx(0.30, rel=REL)
    assert venda.amount == pytest.approx(-30_000.0, rel=REL)
    # Rebalanceamento é jogo de soma zero: o que sai de uma classe entra na outra.
    assert compra.amount + venda.amount == pytest.approx(0.0, abs=1e-9)


def test_dentro_da_banda_fica_ok_mesmo_com_desvio() -> None:
    # 55% contra alvo de 50%: fora do alvo, dentro da banda (40%–60%).
    resultado = compute_rebalance(
        _entradas([("Ações", 0.50), ("FIIs", 0.50)]),
        _carteira([("Ações", 55_000.0), ("FIIs", 45_000.0)]),
    )
    assert _acoes(resultado) == ["OK", "OK"]
    assert resultado.rows[0].deviation == pytest.approx(0.05, rel=REL)


def test_band_relative_customizado() -> None:
    carteira = _carteira([("Ações", 55_000.0), ("FIIs", 45_000.0)])
    alvos = _entradas([("Ações", 0.50), ("FIIs", 0.50)])

    # Banda zero: qualquer desvio vira ordem.
    estrito = compute_rebalance(alvos, carteira, band_relative=0.0)
    assert _acoes(estrito) == ["VENDER", "COMPRAR"]
    assert estrito.band_relative == pytest.approx(0.0, abs=1e-12)

    # Banda larga: 25%–75% engole o desvio.
    largo = compute_rebalance(alvos, carteira, band_relative=0.50)
    assert _acoes(largo) == ["OK", "OK"]

    # Sinal negativo é largura, não direção.
    assert compute_rebalance(alvos, carteira, band_relative=-0.20).band_relative == pytest.approx(
        0.20, rel=REL
    )


# --------------------------------------------------------------------------
# Alvos ausentes, incompletos ou fora de 100%
# --------------------------------------------------------------------------


def test_classe_sem_alvo_entra_com_alvo_zero_e_vira_vender() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.60), ("FIIs", 0.40)]),
        _carteira([("Ações", 60_000.0), ("FIIs", 30_000.0), ("Cripto", 10_000.0)]),
    )
    # Classes sem alvo vão para o fim, depois das que a tabela declarou.
    cripto = resultado.rows[-1]
    assert cripto.asset_class == "Cripto"
    assert cripto.target_weight == pytest.approx(0.0, abs=1e-12)
    assert cripto.band_min == pytest.approx(0.0, abs=1e-12)
    assert cripto.band_max == pytest.approx(0.0, abs=1e-12)
    assert cripto.action == "VENDER"
    assert cripto.amount == pytest.approx(-10_000.0, rel=REL)
    assert any("Cripto" in nota and "alvo 0%" in nota for nota in resultado.notes)


def test_alvo_declarado_para_classe_ausente_da_carteira() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.50), ("Exterior", 0.50)]),
        _carteira([("Ações", 100_000.0)]),
    )
    exterior = resultado.rows[1]
    assert exterior.asset_class == "Exterior"
    assert exterior.current_value == pytest.approx(0.0, abs=1e-12)
    assert exterior.action == "COMPRAR"
    assert exterior.amount == pytest.approx(50_000.0, rel=REL)


def test_sem_alvos_usa_peso_atual_e_avisa() -> None:
    resultado = compute_rebalance(
        _entradas([]),
        _carteira([("Ações", 60_000.0), ("FIIs", 40_000.0)]),
    )
    assert _acoes(resultado) == ["OK", "OK"]
    assert resultado.rows[0].target_weight == pytest.approx(0.60, rel=REL)
    assert resultado.rows[1].target_weight == pytest.approx(0.40, rel=REL)
    assert resultado.targets_sum == pytest.approx(1.0, rel=REL)
    assert all(linha.amount == pytest.approx(0.0, abs=1e-9) for linha in resultado.rows)
    assert any("Nenhum alvo definido" in nota for nota in resultado.notes)


def test_soma_dos_alvos_fora_de_100_gera_nota() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.50), ("FIIs", 0.30)]),
        _carteira([("Ações", 50_000.0), ("FIIs", 50_000.0)]),
    )
    assert resultado.targets_sum == pytest.approx(0.80, rel=REL)
    assert any("soma dos alvos" in nota and "80,0%" in nota for nota in resultado.notes)


def test_soma_dentro_da_tolerancia_nao_gera_nota() -> None:
    # 99,6% está a 0,004 de 100%, dentro da folga de 0,005.
    resultado = compute_rebalance(
        _entradas([("Ações", 0.596), ("FIIs", 0.40)]),
        _carteira([("Ações", 60_000.0), ("FIIs", 40_000.0)]),
    )
    assert not any("soma dos alvos" in nota for nota in resultado.notes)


def test_alvos_repetidos_para_a_mesma_classe_sao_somados() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.30), ("Ações", 0.30), ("FIIs", 0.40)]),
        _carteira([("Ações", 60_000.0), ("FIIs", 40_000.0)]),
    )
    assert len(resultado.rows) == 2
    assert resultado.rows[0].target_weight == pytest.approx(0.60, rel=REL)
    assert resultado.targets_sum == pytest.approx(1.0, rel=REL)


def test_classes_casam_ignorando_acento_e_caixa() -> None:
    resultado = compute_rebalance(
        _entradas([("ACOES", 0.60), ("fiis", 0.40)]),
        _carteira([("Ações", 60_000.0), ("FIIs", 40_000.0)]),
    )
    assert len(resultado.rows) == 2
    # O rótulo exibido é o da carteira, não o digitado na tabela de alvos.
    assert [linha.asset_class for linha in resultado.rows] == ["Ações", "FIIs"]
    assert _acoes(resultado) == ["OK", "OK"]


# --------------------------------------------------------------------------
# Plano de aporte (cash-flow rebalancing)
# --------------------------------------------------------------------------


def test_plano_de_aporte_e_proporcional_ao_deficit() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.40), ("FIIs", 0.35), ("Tesouro Direto", 0.25)], aporte=25_000.0),
        _carteira(
            [("Ações", 20_000.0), ("FIIs", 30_000.0), ("Tesouro Direto", 50_000.0)]
        ),
    )
    plano: List[Dict[str, Any]] = resultado.contribution_plan
    # Só as classes abaixo do alvo: déficit de 20.000 (Ações) e 5.000 (FIIs).
    assert [item["asset_class"] for item in plano] == ["Ações", "FIIs"]
    assert plano[0]["share"] == pytest.approx(0.80, rel=REL)
    assert plano[1]["share"] == pytest.approx(0.20, rel=REL)
    assert plano[0]["amount"] == pytest.approx(20_000.0, rel=REL)
    assert plano[1]["amount"] == pytest.approx(5_000.0, rel=REL)
    # O aporte é distribuído por inteiro.
    assert sum(item["amount"] for item in plano) == pytest.approx(25_000.0, rel=REL)
    assert sum(item["share"] for item in plano) == pytest.approx(1.0, rel=REL)


def test_plano_de_aporte_vem_do_maior_deficit_para_o_menor() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.40), ("FIIs", 0.35), ("Tesouro Direto", 0.25)], aporte=1_000.0),
        _carteira(
            [("Ações", 30_000.0), ("FIIs", 10_000.0), ("Tesouro Direto", 60_000.0)]
        ),
    )
    valores = [item["amount"] for item in resultado.contribution_plan]
    assert valores == sorted(valores, reverse=True)
    assert resultado.contribution_plan[0]["asset_class"] == "FIIs"


def test_plano_vazio_sem_aporte_ou_com_retirada() -> None:
    carteira = _carteira([("Ações", 20_000.0), ("FIIs", 80_000.0)])
    alvos = [("Ações", 0.50), ("FIIs", 0.50)]
    assert compute_rebalance(_entradas(alvos, aporte=0.0), carteira).contribution_plan == []
    assert compute_rebalance(_entradas(alvos, aporte=-5_000.0), carteira).contribution_plan == []


def test_plano_vazio_quando_nenhuma_classe_esta_abaixo_do_alvo() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.60), ("FIIs", 0.40)], aporte=10_000.0),
        _carteira([("Ações", 60_000.0), ("FIIs", 40_000.0)]),
    )
    assert resultado.contribution_plan == []


def test_plano_ignora_classes_dentro_da_banda_mas_acima_do_alvo() -> None:
    # FIIs está acima do alvo e dentro da banda: não recebe aporte nenhum.
    resultado = compute_rebalance(
        _entradas([("Ações", 0.50), ("FIIs", 0.50)], aporte=1_000.0),
        _carteira([("Ações", 45_000.0), ("FIIs", 55_000.0)]),
    )
    assert [item["asset_class"] for item in resultado.contribution_plan] == ["Ações"]
    assert resultado.contribution_plan[0]["amount"] == pytest.approx(1_000.0, rel=REL)


# --------------------------------------------------------------------------
# Notas
# --------------------------------------------------------------------------


def test_notas_trazem_as_diretrizes_da_planilha() -> None:
    resultado = compute_rebalance(_entradas(ALVOS_REFERENCIA), _carteira(CLASSES_REFERENCIA))
    texto = " | ".join(resultado.notes)
    assert "bandas de tolerância de ±20,0%" in texto  # banda relativa, não calendário
    assert "calendário" in texto
    assert "2 semanas" in texto
    assert "APORTES primeiro" in texto
    assert "R$ 20 mil" in texto and "20% sobre o ganho" in texto
    assert "não são gatilho" in texto


def test_nota_de_tolerancia_marca_ordens_ignoraveis() -> None:
    resultado = compute_rebalance(
        _entradas(
            [("Ações", 0.60), ("FIIs", 0.39), ("BDRs", 0.01)],
            tolerancia=500.0,
        ),
        _carteira([("Ações", 60_000.0), ("FIIs", 39_300.0), ("BDRs", 700.0)]),
    )
    bdrs = resultado.rows[2]
    assert bdrs.action == "COMPRAR"
    assert bdrs.amount == pytest.approx(300.0, rel=REL)

    texto = " | ".join(resultado.notes)
    assert "Ordens abaixo de R$ 500,00" in texto
    assert "abaixo da tolerância" in texto
    assert "BDRs (comprar R$ 300,00)" in texto


def test_ordem_acima_da_tolerancia_nao_e_marcada_como_ignoravel() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 0.50), ("FIIs", 0.50)], tolerancia=500.0),
        _carteira([("Ações", 20_000.0), ("FIIs", 80_000.0)]),
    )
    assert not any("abaixo da tolerância" in nota for nota in resultado.notes)
    assert any("Ordens abaixo de R$ 500,00" in nota for nota in resultado.notes)


def test_tolerancia_zerada_omite_a_nota() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 1.00)], tolerancia=0.0),
        _carteira([("Ações", 100_000.0)]),
    )
    assert not any("Ordens abaixo" in nota for nota in resultado.notes)


# --------------------------------------------------------------------------
# Casos degenerados — nada aqui pode levantar exceção
# --------------------------------------------------------------------------


def test_carteira_e_alvos_vazios() -> None:
    resultado = compute_rebalance(WorkbookInputs(), _carteira([]))
    assert resultado.rows == []
    assert resultado.total_value == pytest.approx(0.0, abs=1e-12)
    assert resultado.targets_sum == pytest.approx(0.0, abs=1e-12)
    assert resultado.contribution_plan == []
    assert resultado.notes  # as diretrizes valem mesmo sem carteira


def test_carteira_zerada_com_alvos_nao_divide_por_zero() -> None:
    resultado = compute_rebalance(
        _entradas(ALVOS_REFERENCIA, aporte=1_000.0),
        _carteira([("Ações", 0.0), ("FIIs", 0.0)]),
    )
    assert all(linha.current_weight == pytest.approx(0.0, abs=1e-12) for linha in resultado.rows)
    assert all(linha.amount == pytest.approx(0.0, abs=1e-12) for linha in resultado.rows)
    # Sem patrimônio não há déficit em R$ para ratear.
    assert resultado.contribution_plan == []


def test_entradas_e_carteira_nulas() -> None:
    resultado = compute_rebalance(None, None)  # type: ignore[arg-type]
    assert resultado.rows == []
    assert resultado.total_value == pytest.approx(0.0, abs=1e-12)
    assert resultado.contribution_plan == []


def test_campos_nulos_e_lixo_nao_quebram() -> None:
    entradas = WorkbookInputs(
        rebalance_targets=[
            None,  # type: ignore[list-item]
            RebalanceTarget("", 0.50),  # alvo sem classe: descartado
            RebalanceTarget("Ações", None),  # type: ignore[arg-type]
            RebalanceTarget("FIIs", 0.40),
        ],
        assumptions=Assumptions(monthly_contribution=None, order_tolerance=None),  # type: ignore[arg-type]
    )
    carteira = _carteira([("Ações", 60_000.0), ("FIIs", 40_000.0)])
    carteira.by_class.insert(0, None)  # type: ignore[arg-type]

    resultado = compute_rebalance(entradas, carteira)
    assert [linha.asset_class for linha in resultado.rows] == ["Ações", "FIIs"]
    assert resultado.rows[0].target_weight == pytest.approx(0.0, abs=1e-12)
    assert resultado.rows[0].action == "VENDER"
    assert resultado.contribution_plan == []


def test_total_ausente_cai_para_a_soma_das_classes() -> None:
    carteira = _carteira([("Ações", 60_000.0), ("FIIs", 40_000.0)], total=0.0)
    resultado = compute_rebalance(_entradas([("Ações", 0.60), ("FIIs", 0.40)]), carteira)
    assert resultado.total_value == pytest.approx(100_000.0, rel=REL)
    assert resultado.rows[0].current_weight == pytest.approx(0.60, rel=REL)


def test_band_relative_nulo_cai_no_padrao() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 1.00)]),
        _carteira([("Ações", 100_000.0)]),
        band_relative=None,  # type: ignore[arg-type]
    )
    assert resultado.band_relative == pytest.approx(0.20, rel=REL)


def test_classe_duplicada_na_carteira_e_somada() -> None:
    resultado = compute_rebalance(
        _entradas([("Ações", 1.00)]),
        _carteira([("Ações", 60_000.0), ("ações", 40_000.0)]),
    )
    assert len(resultado.rows) == 1
    assert resultado.rows[0].current_value == pytest.approx(100_000.0, rel=REL)
    assert resultado.rows[0].action == "OK"
