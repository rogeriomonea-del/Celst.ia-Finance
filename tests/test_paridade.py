"""Teste de paridade: pipeline inteiro sobre uma planilha SINTÉTICA em memória.

Nada aqui toca o arquivo real do usuário. Construímos, com openpyxl e em um
``BytesIO``, uma pasta de trabalho com a **mesma estrutura de abas** do original
(Config, Cenarios, Posicao Consolidada, Dolarizado, Posicao_B3, Proventos,
Rebalanceamento, Fluxo de Caixa) mas com dados fictícios pequenos, escolhidos
para que as contas fechem "na mão".

O que se verifica não são números decorados: são as **relações internas** que
precisam valer em qualquer planilha —

* soma dos pesos por classe = 1 e total = soma das classes = soma das posições;
* proventos casados por ticker ⊆ total de proventos do período;
* bandas de rebalanceamento = alvo × 0,8 … alvo × 1,2, e a soma dos ajustes = 0;
* série do simulador = fórmula fechada ``PV(1+i)^n + PMT((1+i)^n − 1)/i``;
* retorno esperado dos cenários = SUMPRODUCT(probabilidade × retorno);
* saldo diário do fluxo = recursão ``saldo(1+d) + aporte − retirada``;
* consolidado: total = Σ contas = Σ categorias = Σ liquidez, e R$ = US$ × câmbio.

Fecha com os casos degenerados (pasta vazia, sem Config, divisores zerados), que
não podem levantar exceção.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from engine import pipeline
from engine.calc.projecao import annual_returns
from engine.calc.simulador import compute_simulation
from engine.models import Analysis, Assumptions

openpyxl = pytest.importorskip("openpyxl")

REL = 1e-9


# --------------------------------------------------------------------------
# Dados sintéticos (fictícios, redondos de propósito)
# --------------------------------------------------------------------------

#: ``(classe, ticker, nome, qtde, preço, var_12m, P/L, P/VP, DY)``
POSICOES: Tuple[Tuple[str, str, str, float, float, Optional[float],
                      Optional[float], Optional[float], Optional[float]], ...] = (
    ("Ações", "AAAA3", "Empresa A", 100.0, 10.0, 0.10, 8.0, None, 0.06),
    ("Ações", "BBBB3", "Empresa B", 200.0, 5.0, 0.20, 12.0, None, 0.08),
    ("FIIs", "CCCC11", "Fundo C", 100.0, 10.0, 0.05, None, 0.90, 0.11),
    ("ETFs", "DDDD11", "ETF D", 50.0, 10.0, -0.10, None, None, None),
    ("Renda Fixa privada", "CDB-E", "CDB do Banco E", 1.0, 500.0, None, None, None, None),
)

#: Valores de mercado: 1.000 + 1.000 + 1.000 + 500 + 500 = 4.000.
TOTAL_CARTEIRA = 4000.0

#: ``(data, tipo, ticker, produto, valor)`` — "ZZZZ11" não está na carteira.
PROVENTOS: Tuple[Tuple[date, str, str, str, float], ...] = (
    (date(2025, 3, 10), "Rendimento", "CCCC11", "CCCC11 - Fundo C", 60.0),
    (date(2025, 4, 10), "Rendimento", "CCCC11", "CCCC11 - Fundo C", 40.0),
    (date(2025, 4, 15), "Dividendo", "AAAA3", "AAAA3 - Empresa A", 50.0),
    (date(2025, 5, 20), "Rendimento", "ZZZZ11", "ZZZZ11 - Fundo de fora", 25.0),
)
TOTAL_PROVENTOS = 175.0
PROVENTOS_CASADOS = 150.0  # só os tickers que existem na carteira

#: Alvos escolhidos para produzir uma VENDER e uma COMPRAR (somam 1,00).
ALVOS: Tuple[Tuple[str, float], ...] = (
    ("Ações", 0.40),            # peso 0,500 > 0,48 -> VENDER
    ("FIIs", 0.25),             # peso 0,250 dentro de [0,20; 0,30] -> OK
    ("ETFs", 0.125),            # peso 0,125 -> OK
    ("Renda Fixa privada", 0.225),  # peso 0,125 < 0,18 -> COMPRAR
)
ACOES_ESPERADAS = {
    "Ações": "VENDER",
    "FIIs": "OK",
    "ETFs": "OK",
    "Renda Fixa privada": "COMPRAR",
}

#: Matriz de cenários: pesos batem com os da carteira sintética.
CENARIOS = ("Péssimo", "Ruim", "Otimista", "Muito Favorável")
MATRIZ: Tuple[Tuple[str, float, Tuple[float, float, float, float]], ...] = (
    ("Ações", 0.50, (-0.10, 0.05, 0.15, 0.25)),
    ("FIIs", 0.25, (0.00, 0.06, 0.12, 0.18)),
    ("ETFs", 0.125, (-0.05, 0.04, 0.10, 0.16)),
    ("Renda Fixa privada", 0.125, (0.12, 0.11, 0.10, 0.09)),
)
PROBABILIDADES = (0.10, 0.30, 0.40, 0.20)

#: Projeção de inflação/Selic — a média das 4 linhas é conferida no teste.
INFLACAO: Tuple[Tuple[int, float, float], ...] = (
    (2026, 0.05, 0.14),
    (2027, 0.04, 0.12),
    (2028, 0.03, 0.10),
    (2029, 0.02, 0.08),
)

CAPITAL_INICIAL = 10000.0
APORTE_MENSAL = 1000.0
RENDA_ALVO = 500.0
CAMBIO = 5.0

#: ``(instituição, categoria, liquidez, valor)``.
CONTAS: Tuple[Tuple[str, str, str, float], ...] = (
    ("Corretora B3", "Investimentos BR", "D+2", TOTAL_CARTEIRA),
    ("Corretora exterior", "Internacional", "D+2 (câmbio)", 1500.0),
    ("Banco X", "Caixa", "Imediata (D0)", 800.0),
    ("Banco Y", "Caixa", "Imediata (D0)", 200.0),
    ("Reserva", "Reserva", "Reserva", 1000.0),
)
TOTAL_CONSOLIDADO = 7500.0

#: ``(corretora, ticker, descrição, qtde, preço US$, custo US$)``.
EXTERIOR: Tuple[Tuple[str, str, str, float, float, float], ...] = (
    ("Corretora Alfa", "XPTO", "Ação estrangeira XPTO", 10.0, 20.0, 180.0),
    ("Corretora Alfa", "WXYZ", "Ação estrangeira WXYZ", 5.0, 8.0, 50.0),
    ("Corretora Beta", "QQQQ", "ETF QQQQ", 4.0, 25.0, 110.0),
)
EXTERIOR_TOTAL_USD = 10 * 20.0 + 5 * 8.0 + 4 * 25.0  # 340
EXTERIOR_CUSTO_USD = 180.0 + 50.0 + 110.0  # 340 -> resultado 0 de propósito

#: Fluxo de caixa diário: 90 dias a partir de 01/01/2025.
DIAS_FLUXO = 90
FLUXO_INICIO = date(2025, 1, 1)


def _linhas_fluxo() -> List[Tuple[date, float, float, float, float]]:
    """``(dia, entradas, aporte investimentos, aporte reserva, gastos)``.

    Entrada de 3.000 e aporte de 1.000 todo dia 5; gasto de 500 todo dia 20.
    """
    linhas: List[Tuple[date, float, float, float, float]] = []
    for passo in range(DIAS_FLUXO):
        dia = FLUXO_INICIO + timedelta(days=passo)
        entrada = 3000.0 if dia.day == 5 else 0.0
        aporte = 1000.0 if dia.day == 5 else 0.0
        gasto = 500.0 if dia.day == 20 else 0.0
        linhas.append((dia, entrada, aporte, 0.0, gasto))
    return linhas


# --------------------------------------------------------------------------
# Construção da pasta de trabalho sintética
# --------------------------------------------------------------------------


def _escrever(aba: Any, linha: int, coluna: int, valores: Sequence[Any]) -> None:
    for deslocamento, valor in enumerate(valores):
        if valor is not None:
            aba.cell(row=linha, column=coluna + deslocamento, value=valor)


def _aba_config(livro: Any) -> None:
    aba = livro.create_sheet("Config")
    aba["A1"] = "Configurações da Carteira (edite células azuis)"
    parametros: Tuple[Tuple[str, Any], ...] = (
        ("Cenário selecionado", "Valorizacao"),
        ("Capital inicial (R$)", CAPITAL_INICIAL),
        ("Aporte mensal (R$)", APORTE_MENSAL),
        ("Inflação esperada (IPCA, a.a.)", 0.04),
        ("Retorno nominal Selic / pós-fixado (a.a.)", 0.14),
        ("Retorno real IPCA+ longo (a.a.)", 0.05),
        ("Δ taxa real no cenário de corte (p.p.)", -0.01),
        ("Duration estimada IPCA+ 2050 (anos)", 20.0),
        ("DY alvo FIIs (a.a.)", 0.09),
        ("DY alvo Ações (a.a.)", 0.07),
        ("Retorno preço Ações (a.a.)", 0.06),
        ("Retorno preço FIIs (a.a.)", 0.03),
        ("Retorno exterior (a.a.)", 0.04),
        ("Tolerância p/ ignorar ordens (R$)", 100.0),
        ("Data inicial projeções", date(2025, 1, 1)),
        ("Data final projeções", date(2025, 12, 31)),
        ("Renda mensal alvo (aprox.)", RENDA_ALVO),
        ("Retirada mensal planejada (R$)", 0.0),
    )
    for indice, (rotulo, valor) in enumerate(parametros, start=3):
        aba.cell(row=indice, column=1, value=rotulo)
        aba.cell(row=indice, column=2, value=valor)


def _aba_cenarios(livro: Any) -> None:
    aba = livro.create_sheet("Cenarios")
    aba["B1"] = "CENÁRIOS DE RETORNO DA CARTEIRA E INFLAÇÃO PROJETADA"
    aba["B4"] = "1. INFLAÇÃO PROJETADA — MEDIANA FOCUS/BCB"
    _escrever(aba, 5, 2, ("Ano", "IPCA proj. (a.a.)", "Selic fim de ano"))
    for indice, (ano, ipca, selic) in enumerate(INFLACAO, start=6):
        _escrever(aba, indice, 2, (ano, ipca, selic))

    aba["B18"] = "3. MATRIZ DE CENÁRIOS — RETORNO NOMINAL POR CLASSE"
    _escrever(aba, 19, 2, ("Classe", "Peso na carteira B3") + CENARIOS)
    for indice, (nome, peso, retornos) in enumerate(MATRIZ, start=20):
        _escrever(aba, indice, 2, (nome, peso) + retornos)
    _escrever(aba, 24, 2, ("Total", sum(p for _, p, _ in MATRIZ)))
    _escrever(aba, 25, 2, ("Probabilidade do cenário", 1.0) + PROBABILIDADES)


def _aba_consolidada(livro: Any) -> None:
    aba = livro.create_sheet("Posicao Consolidada")
    aba["B2"] = "POSIÇÃO CONSOLIDADA"
    aba["B5"] = "PARÂMETROS"
    _escrever(aba, 6, 2, ("Câmbio USD/BRL", CAMBIO))
    _escrever(aba, 7, 2, ("Reserva de oportunidade (R$)", 1000.0))
    _escrever(aba, 14, 2, ("Instituição / Classe", "Categoria", "Liquidez", "Valor (R$)"))
    for indice, (nome, categoria, liquidez, valor) in enumerate(CONTAS, start=15):
        _escrever(aba, indice, 2, (nome, categoria, liquidez, valor))
    _escrever(aba, 15 + len(CONTAS), 2, ("TOTAL", "", "", TOTAL_CONSOLIDADO))


def _aba_dolarizado(livro: Any) -> None:
    aba = livro.create_sheet("Dolarizado")
    aba["B2"] = "DOLARIZADO — ATIVOS INTERNACIONAIS"
    _escrever(aba, 5, 2, ("Câmbio USD/BRL:", CAMBIO))
    _escrever(
        aba, 7, 2,
        ("Corretora", "Ticker", "Descrição", "Qtde", "Preço (US$)", "Valor (US$)",
         "Resultado (US$)", "Var %", "% do total", "Valor (R$)", "", "Custo (US$)"),
    )
    for indice, (corretora, ticker, descricao, qtde, preco, custo) in enumerate(EXTERIOR, start=8):
        _escrever(aba, indice, 2, (corretora, ticker, descricao, qtde, preco))
        aba.cell(row=indice, column=13, value=custo)


def _aba_posicoes(livro: Any) -> None:
    aba = livro.create_sheet("Posicao_B3")
    aba["B2"] = "POSIÇÃO REAL — B3"
    _escrever(
        aba, 8, 2,
        ("Classe", "Ticker", "Ativo", "Qtde", "Preço (R$)", "Valor (R$)", "% Carteira",
         "Var 12m", "Proventos período (R$)", "Obs.", "P/L", "P/VP", "DY 12m",
         "Sinal mercado (téc.)", "Leitura fundamentalista"),
    )
    for indice, item in enumerate(POSICOES, start=9):
        classe, ticker, nome, qtde, preco, var, pl, pvp, dy = item
        _escrever(aba, indice, 2, (classe, ticker, nome, qtde, preco, qtde * preco, None, var))
        aba.cell(row=indice, column=12, value=pl)
        aba.cell(row=indice, column=13, value=pvp)
        aba.cell(row=indice, column=14, value=dy)


def _aba_proventos(livro: Any) -> None:
    aba = livro.create_sheet("Proventos")
    aba["B2"] = "PROVENTOS RECEBIDOS"
    _escrever(aba, 6, 2, ("Data", "Tipo", "Ticker/Código", "Produto", "Valor (R$)"))
    for indice, (dia, tipo, ticker, produto, valor) in enumerate(PROVENTOS, start=7):
        _escrever(aba, indice, 2, (dia, tipo, ticker, produto, valor))


def _aba_rebalanceamento(livro: Any) -> None:
    aba = livro.create_sheet("Rebalanceamento")
    aba["B2"] = "REBALANCEAMENTO — DIRETRIZES E MONITOR"
    _escrever(
        aba, 18, 2,
        ("Classe", "Valor atual (R$)", "% atual", "% alvo (edite)",
         "Banda mín.", "Banda máx.", "AÇÃO"),
    )
    for indice, (classe, alvo) in enumerate(ALVOS, start=19):
        _escrever(aba, indice, 2, (classe, None, None, alvo, alvo * 0.8, alvo * 1.2))
    _escrever(aba, 19 + len(ALVOS), 2, ("Soma dos alvos (deve ser 100%)", None, None, 1.0))


def _aba_fluxo(livro: Any) -> None:
    aba = livro.create_sheet("Fluxo de Caixa")
    _escrever(
        aba, 8, 2,
        ("Data_aux", "Data", "Entradas TT", "Entradas", "Investimentos",
         "Reserva de emergencia", "Aporte Investimentos", "Aporte Reserva",
         "Fatura cartao", "Gastos"),
    )
    for indice, (dia, entrada, aporte, reserva, gasto) in enumerate(_linhas_fluxo(), start=9):
        aba.cell(row=indice, column=2, value=dia)   # Data_aux
        aba.cell(row=indice, column=3, value=dia)   # Data
        aba.cell(row=indice, column=5, value=entrada)
        aba.cell(row=indice, column=8, value=aporte)
        aba.cell(row=indice, column=9, value=reserva)
        aba.cell(row=indice, column=11, value=gasto)


def construir_planilha() -> bytes:
    """Monta a pasta sintética em memória e devolve os bytes do .xlsx."""
    livro = openpyxl.Workbook()
    livro.remove(livro.active)
    _aba_config(livro)
    _aba_cenarios(livro)
    _aba_consolidada(livro)
    _aba_dolarizado(livro)
    _aba_posicoes(livro)
    _aba_proventos(livro)
    _aba_rebalanceamento(livro)
    _aba_fluxo(livro)
    buffer = io.BytesIO()
    livro.save(buffer)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def analise() -> Analysis:
    """Roda o pipeline inteiro uma vez sobre a planilha sintética."""
    return pipeline.analyze_bytes(construir_planilha(), "sintetica.xlsx")


# --------------------------------------------------------------------------
# O pipeline roda por inteiro
# --------------------------------------------------------------------------


def test_pipeline_produz_todos_os_blocos(analise: Analysis) -> None:
    """Nenhum bloco pode voltar ``None`` numa planilha bem formada."""
    assert analise.portfolio is not None
    assert analise.proventos is not None
    assert analise.rebalance is not None
    assert analise.simulation is not None
    assert analise.projection is not None
    assert analise.scenarios is not None
    assert analise.cashflow is not None
    assert analise.consolidated is not None
    assert analise.charts


def test_nenhum_bloco_falhou(analise: Analysis) -> None:
    """Avisos de 'não foi possível calcular' denunciam exceção engolida."""
    quebrados = [a for a in analise.warnings if "não foi possível calcular" in a.lower()]
    assert quebrados == []


def test_resultado_serializa_em_json(analise: Analysis) -> None:
    import json

    from engine.models import to_dict

    assert json.loads(json.dumps(to_dict(analise)))["portfolio"]["total_value"] == pytest.approx(
        TOTAL_CARTEIRA, rel=REL
    )


# --------------------------------------------------------------------------
# Carteira: total = Σ classes = Σ posições, e os pesos somam 1
# --------------------------------------------------------------------------


def test_total_bate_com_a_soma_das_posicoes(analise: Analysis) -> None:
    carteira = analise.portfolio
    assert carteira is not None
    assert carteira.total_value == pytest.approx(TOTAL_CARTEIRA, rel=REL)
    assert sum(p.market_value for p in carteira.positions) == pytest.approx(
        carteira.total_value, rel=REL
    )
    assert carteira.positions_count == len(POSICOES)


def test_total_bate_com_a_soma_das_classes(analise: Analysis) -> None:
    carteira = analise.portfolio
    assert carteira is not None
    assert sum(c.value for c in carteira.by_class) == pytest.approx(carteira.total_value, rel=REL)
    assert sum(c.positions for c in carteira.by_class) == carteira.positions_count


def test_pesos_por_classe_somam_um(analise: Analysis) -> None:
    carteira = analise.portfolio
    assert carteira is not None
    assert sum(c.weight for c in carteira.by_class) == pytest.approx(1.0, rel=REL)
    for classe in carteira.by_class:
        assert classe.weight == pytest.approx(classe.value / carteira.total_value, rel=REL)


def test_pesos_por_posicao_somam_um(analise: Analysis) -> None:
    carteira = analise.portfolio
    assert carteira is not None
    assert sum(p.portfolio_weight for p in carteira.positions) == pytest.approx(1.0, rel=REL)


def test_var_12m_ponderada_ignora_renda_fixa(analise: Analysis) -> None:
    """Só as classes listadas entram, com o peso renormalizado entre elas."""
    listadas = [
        (q * p, v)
        for classe, _, _, q, p, v, _, _, _ in POSICOES
        if v is not None and classe in ("Ações", "FIIs", "ETFs", "BDRs", "Fundos listados")
    ]
    esperado = sum(valor * var for valor, var in listadas) / sum(valor for valor, _ in listadas)
    carteira = analise.portfolio
    assert carteira is not None
    assert carteira.weighted_var_12m == pytest.approx(esperado, rel=REL)


# --------------------------------------------------------------------------
# Proventos: casamento por ticker (SUMIF)
# --------------------------------------------------------------------------


def test_total_de_proventos_e_a_soma_dos_lancamentos(analise: Analysis) -> None:
    proventos = analise.proventos
    assert proventos is not None
    assert proventos.total == pytest.approx(TOTAL_PROVENTOS, rel=REL)
    assert sum(item["amount"] for item in proventos.by_ticker) == pytest.approx(
        proventos.total, rel=REL
    )
    assert sum(item["amount"] for item in proventos.by_month) == pytest.approx(
        proventos.total, rel=REL
    )


def test_proventos_casados_excluem_ticker_de_fora(analise: Analysis) -> None:
    """``ZZZZ11`` não está na carteira: entra no total, não no casado."""
    carteira = analise.portfolio
    assert carteira is not None
    assert carteira.total_proventos == pytest.approx(PROVENTOS_CASADOS, rel=REL)
    assert carteira.total_proventos < TOTAL_PROVENTOS
    por_ticker = {p.ticker: p.proventos_period for p in carteira.positions}
    assert por_ticker["CCCC11"] == pytest.approx(100.0, rel=REL)
    assert por_ticker["AAAA3"] == pytest.approx(50.0, rel=REL)
    assert por_ticker["DDDD11"] == pytest.approx(0.0, abs=1e-12)
    assert sum(por_ticker.values()) == pytest.approx(carteira.total_proventos, rel=REL)


def test_top_de_proventos_esta_ordenado(analise: Analysis) -> None:
    proventos = analise.proventos
    assert proventos is not None
    valores = [item["amount"] for item in proventos.top]
    assert valores == sorted(valores, reverse=True)
    assert proventos.top[0]["amount"] == pytest.approx(100.0, rel=REL)


# --------------------------------------------------------------------------
# Rebalanceamento: bandas de 20% relativas
# --------------------------------------------------------------------------


def test_alvos_somam_um(analise: Analysis) -> None:
    rebal = analise.rebalance
    assert rebal is not None
    assert rebal.targets_sum == pytest.approx(1.0, rel=REL)
    assert rebal.band_relative == pytest.approx(0.20, rel=REL)


def test_bandas_sao_o_alvo_mais_ou_menos_vinte_por_cento(analise: Analysis) -> None:
    rebal = analise.rebalance
    assert rebal is not None
    for linha in rebal.rows:
        assert linha.band_min == pytest.approx(linha.target_weight * 0.8, rel=REL)
        assert linha.band_max == pytest.approx(linha.target_weight * 1.2, rel=REL)


def test_acao_segue_a_banda(analise: Analysis) -> None:
    rebal = analise.rebalance
    assert rebal is not None
    obtido = {linha.asset_class: linha.action for linha in rebal.rows}
    assert obtido == ACOES_ESPERADAS
    for linha in rebal.rows:
        if linha.current_weight < linha.band_min:
            assert linha.action == "COMPRAR"
        elif linha.current_weight > linha.band_max:
            assert linha.action == "VENDER"
        else:
            assert linha.action == "OK"


def test_ajustes_se_anulam(analise: Analysis) -> None:
    """Σ (alvo − peso) × total = 0: rebalancear não cria nem destrói dinheiro."""
    rebal = analise.rebalance
    assert rebal is not None
    assert sum(linha.amount for linha in rebal.rows) == pytest.approx(0.0, abs=1e-6)
    assert sum(linha.deviation for linha in rebal.rows) == pytest.approx(0.0, abs=1e-12)
    assert rebal.total_value == pytest.approx(TOTAL_CARTEIRA, rel=REL)


def test_plano_de_aporte_nao_excede_o_aporte(analise: Analysis) -> None:
    rebal = analise.rebalance
    assert rebal is not None
    if rebal.contribution_plan:
        distribuido = sum(item["amount"] for item in rebal.contribution_plan)
        assert distribuido <= APORTE_MENSAL + 1e-9


# --------------------------------------------------------------------------
# Cenários: SUMPRODUCT peso × retorno e probabilidade × retorno
# --------------------------------------------------------------------------


def test_retorno_por_cenario_e_sumproduct_dos_pesos(analise: Analysis) -> None:
    cenarios = analise.scenarios
    assert cenarios is not None
    for indice, nome in enumerate(CENARIOS):
        esperado = sum(peso * retornos[indice] for _, peso, retornos in MATRIZ)
        assert cenarios.returns_by_scenario[nome] == pytest.approx(esperado, rel=REL)


def test_retorno_esperado_e_sumproduct_das_probabilidades(analise: Analysis) -> None:
    cenarios = analise.scenarios
    assert cenarios is not None
    esperado = sum(
        probabilidade * cenarios.returns_by_scenario[nome]
        for nome, probabilidade in zip(CENARIOS, PROBABILIDADES)
    )
    assert cenarios.expected_return == pytest.approx(esperado, rel=REL)
    assert sum(cenarios.probabilities.values()) == pytest.approx(1.0, rel=REL)


def test_medias_de_inflacao_e_selic(analise: Analysis) -> None:
    cenarios = analise.scenarios
    assert cenarios is not None
    assert cenarios.inflation_average == pytest.approx(
        sum(i for _, i, _ in INFLACAO) / len(INFLACAO), rel=REL
    )
    assert cenarios.selic_average == pytest.approx(
        sum(s for _, _, s in INFLACAO) / len(INFLACAO), rel=REL
    )


def test_indicadores_ponderados_contam_quem_nao_tem_o_dado(analise: Analysis) -> None:
    """``SUMPRODUCT(...)/SUMIFS(...)``: sem o indicador, entra 0 no numerador.

    O denominador é o valor de **toda** a classe, então DDDD11 (ETF, sem DY)
    não afeta ações nem FIIs, mas BBBB3 sem P/VP afetaria — é o comportamento
    do Excel, e é o que se confere aqui para ações e FIIs.
    """
    cenarios = analise.scenarios
    assert cenarios is not None
    valor_acoes = sum(q * p for c, _, _, q, p, *_ in POSICOES if c == "Ações")
    dy_acoes = sum(
        q * p * (dy or 0.0) for c, _, _, q, p, _, _, _, dy in POSICOES if c == "Ações"
    ) / valor_acoes
    assert cenarios.weighted_dy_stocks == pytest.approx(dy_acoes, rel=REL)

    valor_fiis = sum(q * p for c, _, _, q, p, *_ in POSICOES if c == "FIIs")
    pvp_fiis = sum(
        q * p * (pvp or 0.0) for c, _, _, q, p, _, _, pvp, _ in POSICOES if c == "FIIs"
    ) / valor_fiis
    assert cenarios.weighted_pb_fii == pytest.approx(pvp_fiis, rel=REL)


# --------------------------------------------------------------------------
# Simulador: série recursiva × fórmula fechada
# --------------------------------------------------------------------------


def test_simulador_bate_com_a_formula_fechada(analise: Analysis) -> None:
    """O painel usa a fórmula fechada arredondada a centavos, como a planilha.

    A série, por ser recursiva, fica sem arredondamento — os dois caminhos têm
    de coincidir até o centavo.
    """
    simulacao = analise.simulation
    assert simulacao is not None
    taxa = simulacao.monthly_rate
    assert taxa == pytest.approx((1 + simulacao.annual_rate) ** (1 / 12) - 1, rel=REL)

    meses = simulacao.months
    fechada = CAPITAL_INICIAL * (1 + taxa) ** meses + APORTE_MENSAL * (
        ((1 + taxa) ** meses - 1) / taxa
    )
    assert simulacao.final_balance == pytest.approx(round(fechada, 2), rel=REL)
    assert simulacao.series[-1].balance == pytest.approx(fechada, rel=REL)


def test_serie_do_simulador_e_a_recursao(analise: Analysis) -> None:
    """``saldo = saldo × (1+i) + PMT``, mês a mês, partindo do mês 0."""
    simulacao = analise.simulation
    assert simulacao is not None
    taxa = simulacao.monthly_rate
    assert simulacao.series[0].month == 0
    assert simulacao.series[0].balance == pytest.approx(CAPITAL_INICIAL, rel=REL)

    saldo = CAPITAL_INICIAL
    for ponto in simulacao.series[1:]:
        saldo = saldo * (1 + taxa) + APORTE_MENSAL
        assert ponto.balance == pytest.approx(saldo, rel=REL)
        assert ponto.invested == pytest.approx(
            CAPITAL_INICIAL + APORTE_MENSAL * ponto.month, rel=REL
        )
        assert ponto.interest == pytest.approx(ponto.balance - ponto.invested, rel=REL)


def test_investido_mais_juros_fecha_o_saldo(analise: Analysis) -> None:
    simulacao = analise.simulation
    assert simulacao is not None
    assert simulacao.total_invested == pytest.approx(
        CAPITAL_INICIAL + APORTE_MENSAL * simulacao.months, rel=REL
    )
    assert simulacao.total_invested + simulacao.total_interest == pytest.approx(
        simulacao.final_balance, rel=REL
    )


def test_simulador_com_taxa_zero_e_soma_simples() -> None:
    """``i == 0`` cai em ``PV + PMT × n`` sem divisão por zero."""
    premissas = Assumptions(initial_capital=1000.0, monthly_contribution=100.0)
    resultado = compute_simulation(premissas, 0.0, 12)
    assert resultado.monthly_rate == pytest.approx(0.0, abs=1e-15)
    assert resultado.final_balance == pytest.approx(1000.0 + 100.0 * 12, rel=REL)
    assert resultado.total_interest == pytest.approx(0.0, abs=1e-9)


def test_simulador_aceita_retirada() -> None:
    """Aporte negativo é retirada — permitido, e a série tem de cair."""
    premissas = Assumptions(initial_capital=100000.0, monthly_contribution=-1000.0)
    resultado = compute_simulation(premissas, 0.0, 12)
    assert resultado.final_balance == pytest.approx(100000.0 - 12000.0, rel=REL)
    assert resultado.series[-1].balance < resultado.series[0].balance


# --------------------------------------------------------------------------
# Projeção: fórmula dos dois regimes e recursão mensal
# --------------------------------------------------------------------------


def test_retornos_anuais_seguem_a_formula_do_config(analise: Analysis) -> None:
    premissas = analise.assumptions
    selic = premissas.selic_annual
    ipca_mais = premissas.inflation_annual + premissas.real_rate_annual
    acoes = premissas.dy_stocks + premissas.price_return_stocks
    fiis = premissas.dy_fii + premissas.price_return_fii
    exterior = premissas.foreign_return

    renda_esperada = (
        0.175 * selic
        + 0.10 * ipca_mais
        + 0.07 * ipca_mais
        + 0.10 * ipca_mais
        + 0.25 * fiis
        + 0.205 * acoes
        + 0.05 * acoes
        + 0.05 * exterior
    )
    marcacao = -premissas.duration_years * premissas.real_rate_delta
    valorizacao_esperada = (
        0.095 * selic
        + 0.05 * ipca_mais
        + 0.15 * (ipca_mais + marcacao)
        + 0.20 * (fiis + 0.03)
        + 0.305 * (acoes + 0.02)
        + 0.10 * (acoes + 0.03)
        + 0.10 * exterior
    )

    renda, valorizacao = annual_returns(premissas)
    assert renda == pytest.approx(renda_esperada, rel=REL)
    assert valorizacao == pytest.approx(valorizacao_esperada, rel=REL)

    projecao = analise.projection
    assert projecao is not None
    assert projecao.annual_return_income == pytest.approx(renda_esperada, rel=REL)
    assert projecao.annual_return_growth == pytest.approx(valorizacao_esperada, rel=REL)


def test_serie_da_projecao_e_a_recursao_mensal(analise: Analysis) -> None:
    """``valor_t = valor_{t-1} × (1 + taxa_mensal) + aporte``."""
    projecao = analise.projection
    premissas = analise.assumptions
    assert projecao is not None

    aporte = premissas.monthly_contribution - premissas.monthly_withdrawal
    taxa_renda = (1 + projecao.annual_return_income) ** (1 / 12) - 1
    taxa_valorizacao = (1 + projecao.annual_return_growth) ** (1 / 12) - 1

    renda, valorizacao = CAPITAL_INICIAL, CAPITAL_INICIAL
    for ponto in projecao.series:
        renda = renda * (1 + taxa_renda) + aporte
        valorizacao = valorizacao * (1 + taxa_valorizacao) + aporte
        assert ponto.value_income == pytest.approx(renda, rel=REL)
        assert ponto.value_growth == pytest.approx(valorizacao, rel=REL)
        assert ponto.contribution == pytest.approx(aporte, rel=REL)


def test_renda_mensal_cresce_pela_inflacao(analise: Analysis) -> None:
    projecao = analise.projection
    premissas = analise.assumptions
    assert projecao is not None
    inflacao_mensal = (1 + premissas.inflation_annual) ** (1 / 12) - 1

    assert projecao.series[0].income_income == pytest.approx(RENDA_ALVO, rel=REL)
    for anterior, atual in zip(projecao.series, projecao.series[1:]):
        assert atual.income_income == pytest.approx(
            anterior.income_income * (1 + inflacao_mensal), rel=REL
        )


def test_regime_selecionado_segue_o_config(analise: Analysis) -> None:
    """Config diz "Valorizacao", então o selecionado é o regime de valorização."""
    projecao = analise.projection
    assert projecao is not None
    for ponto in projecao.series:
        assert ponto.value_selected == pytest.approx(ponto.value_growth, rel=REL)
        assert ponto.income_selected == pytest.approx(ponto.income_growth, rel=REL)


# --------------------------------------------------------------------------
# Fluxo de caixa: recursão diária e baldes mensais
# --------------------------------------------------------------------------


def test_saldo_diario_segue_a_recursao(analise: Analysis) -> None:
    """``saldo_t = saldo_{t-1} × (1+d) + aporte_t − retirada_t``, ``d`` diária."""
    fluxo = analise.cashflow
    cenarios = analise.scenarios
    assert fluxo is not None and cenarios is not None

    diaria = (1 + cenarios.expected_return) ** (1 / 365) - 1
    esperados: Dict[date, float] = {}
    saldo = CAPITAL_INICIAL
    for dia, _entrada, aporte, _reserva, _gasto in _linhas_fluxo():
        saldo = saldo * (1 + diaria) + aporte
        esperados[dia] = saldo

    assert fluxo.final_invested == pytest.approx(saldo, rel=REL)
    # A série publicada é amostrada para o gráfico; cada ponto que sobrevive
    # tem de bater com a recursão completa.
    assert fluxo.daily_balance
    for ponto in fluxo.daily_balance:
        assert ponto["balance"] == pytest.approx(esperados[ponto["day"]], rel=REL)


def test_mensal_e_a_soma_dos_dias(analise: Analysis) -> None:
    fluxo = analise.cashflow
    assert fluxo is not None
    linhas = _linhas_fluxo()

    entradas = sum(e for _, e, _, _, _ in linhas)
    gastos = sum(g for _, _, _, _, g in linhas)
    aportes = sum(a for _, _, a, _, _ in linhas)

    assert sum(m.inflow for m in fluxo.monthly) == pytest.approx(entradas, rel=REL)
    assert sum(m.expenses for m in fluxo.monthly) == pytest.approx(gastos, rel=REL)
    assert sum(m.invest_contribution for m in fluxo.monthly) == pytest.approx(aportes, rel=REL)
    assert fluxo.total_inflow == pytest.approx(entradas, rel=REL)
    assert fluxo.total_expenses == pytest.approx(gastos, rel=REL)
    assert fluxo.total_contributions == pytest.approx(aportes, rel=REL)


def test_savings_rate_e_a_sobra_sobre_as_entradas(analise: Analysis) -> None:
    fluxo = analise.cashflow
    assert fluxo is not None
    esperado = (fluxo.total_inflow - fluxo.total_expenses) / fluxo.total_inflow
    assert fluxo.savings_rate == pytest.approx(esperado, rel=REL)
    for mes in fluxo.monthly:
        assert mes.savings == pytest.approx(mes.inflow - mes.expenses, rel=REL)
        assert mes.net_worth == pytest.approx(mes.invested_balance + mes.cash_balance, rel=REL)


def test_caixa_mensal_e_acumulado(analise: Analysis) -> None:
    """Saldo de caixa = acumulado de entradas − gastos − aportes."""
    fluxo = analise.cashflow
    assert fluxo is not None
    caixa = 0.0
    por_mes: Dict[date, float] = {}
    for dia, entrada, aporte, reserva, gasto in _linhas_fluxo():
        caixa += entrada - gasto - aporte - reserva
        por_mes[date(dia.year, dia.month, 1)] = caixa
    for mes in fluxo.monthly:
        assert mes.cash_balance == pytest.approx(por_mes[mes.month], rel=REL)


# --------------------------------------------------------------------------
# Consolidado e exterior
# --------------------------------------------------------------------------


def test_consolidado_fecha_por_categoria_e_por_liquidez(analise: Analysis) -> None:
    consolidado = analise.consolidated
    assert consolidado is not None
    assert consolidado.total == pytest.approx(TOTAL_CONSOLIDADO, rel=REL)
    assert sum(c["value"] for c in consolidado.accounts) == pytest.approx(
        consolidado.total, rel=REL
    )
    assert sum(c["value"] for c in consolidado.by_category) == pytest.approx(
        consolidado.total, rel=REL
    )
    assert sum(c["value"] for c in consolidado.by_liquidity) == pytest.approx(
        consolidado.total, rel=REL
    )


def test_exterior_converte_pelo_cambio(analise: Analysis) -> None:
    consolidado = analise.consolidated
    assert consolidado is not None
    assert consolidado.usd_brl == pytest.approx(CAMBIO, rel=REL)
    assert consolidado.foreign_total_usd == pytest.approx(EXTERIOR_TOTAL_USD, rel=REL)
    assert consolidado.foreign_total_brl == pytest.approx(
        EXTERIOR_TOTAL_USD * CAMBIO, rel=REL
    )
    assert consolidado.foreign_result_usd == pytest.approx(
        EXTERIOR_TOTAL_USD - EXTERIOR_CUSTO_USD, abs=1e-9
    )
    assert len(consolidado.foreign) == len(EXTERIOR)


def test_subtotais_por_corretora_somam_o_total(analise: Analysis) -> None:
    consolidado = analise.consolidated
    assert consolidado is not None
    total = sum(b["value_usd"] for b in consolidado.foreign_by_broker)
    assert total == pytest.approx(consolidado.foreign_total_usd, rel=REL)
    por_corretora = {b["broker"]: b["value_usd"] for b in consolidado.foreign_by_broker}
    assert por_corretora["Corretora Alfa"] == pytest.approx(10 * 20.0 + 5 * 8.0, rel=REL)
    assert por_corretora["Corretora Beta"] == pytest.approx(4 * 25.0, rel=REL)


# --------------------------------------------------------------------------
# Gráficos
# --------------------------------------------------------------------------


IDS_ESPERADOS = (
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


def test_os_nove_graficos_saem(analise: Analysis) -> None:
    obtidos = [g.id for g in analise.charts]
    assert obtidos == list(IDS_ESPERADOS)
    for grafico in analise.charts:
        assert grafico.data, f"gráfico {grafico.id} veio sem dados"
        assert grafico.series
        for ponto in grafico.data:
            assert grafico.x_key in ponto


def test_donut_de_classes_reproduz_a_carteira(analise: Analysis) -> None:
    carteira = analise.portfolio
    donut = next(g for g in analise.charts if g.id == "alocacao-classe")
    assert carteira is not None
    assert sum(p["value"] for p in donut.data) == pytest.approx(carteira.total_value, rel=REL)


# --------------------------------------------------------------------------
# Casos degenerados: nada pode levantar exceção
# --------------------------------------------------------------------------


def test_pasta_vazia_nao_quebra() -> None:
    livro = openpyxl.Workbook()
    livro.active.title = "Vazia"
    buffer = io.BytesIO()
    livro.save(buffer)

    resultado = pipeline.analyze_bytes(buffer.getvalue(), "vazia.xlsx")
    assert resultado.portfolio is None or resultado.portfolio.total_value == pytest.approx(0.0)
    assert resultado.warnings


def test_planilha_so_com_posicoes_ainda_produz_carteira() -> None:
    """Sem Config, sem Cenarios, sem Fluxo: a carteira e os gráficos saem."""
    livro = openpyxl.Workbook()
    livro.remove(livro.active)
    _aba_posicoes(livro)
    buffer = io.BytesIO()
    livro.save(buffer)

    resultado = pipeline.analyze_bytes(buffer.getvalue(), "so_posicoes.xlsx")
    assert resultado.portfolio is not None
    assert resultado.portfolio.total_value == pytest.approx(TOTAL_CARTEIRA, rel=REL)
    assert sum(c.weight for c in resultado.portfolio.by_class) == pytest.approx(1.0, rel=REL)
    assert any(g.id == "alocacao-classe" for g in resultado.charts)


def test_posicoes_com_preco_zero_nao_dividem_por_zero() -> None:
    """Carteira inteira valendo zero: pesos viram 0,0 em vez de estourar."""
    livro = openpyxl.Workbook()
    livro.remove(livro.active)
    aba = livro.create_sheet("Posicao_B3")
    _escrever(aba, 8, 2, ("Classe", "Ticker", "Ativo", "Qtde", "Preço (R$)"))
    _escrever(aba, 9, 2, ("Ações", "AAAA3", "Empresa A", 100.0, 0.0))
    _escrever(aba, 10, 2, ("FIIs", "CCCC11", "Fundo C", 50.0, 0.0))
    buffer = io.BytesIO()
    livro.save(buffer)

    resultado = pipeline.analyze_bytes(buffer.getvalue(), "zerada.xlsx")
    carteira = resultado.portfolio
    assert carteira is not None
    assert carteira.total_value == pytest.approx(0.0, abs=1e-12)
    assert all(c.weight == 0.0 for c in carteira.by_class)
    assert all(p.portfolio_weight == 0.0 for p in carteira.positions)
    assert carteira.weighted_var_12m == 0.0
