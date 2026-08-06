"""Testes de ``engine/pipeline.py``, ``engine/cli.py`` e ``engine/server.py``.

Todas as fixtures são **sintéticas**: um CSV de duas linhas e uma pasta de
trabalho montada com openpyxl na memória. Nada de arquivo do usuário, nada de
rede — as cotações são injetadas por ``monkeypatch``.
"""

from __future__ import annotations

import io
import json
import sys
import base64
import threading
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from engine import cli, pipeline, quotes, server  # noqa: E402
from engine.models import Assumptions, ScenarioResult, to_dict  # noqa: E402

CSV_SIMPLES = "Ticker;Quantidade;Preco\r\nPETR4;100;38,50\r\nBBAS3;200;25,10\r\n".encode("utf-8")


# --------------------------------------------------------------------------
# Fixtures sintéticas
# --------------------------------------------------------------------------


def _xlsx_completo() -> bytes:
    """Pasta de trabalho mínima com Config, Posicao_B3, Proventos e Cenarios."""
    openpyxl = pytest.importorskip("openpyxl")
    livro = openpyxl.Workbook()

    config = livro.active
    config.title = "Config"
    for linha, (rotulo, valor) in enumerate(
        [
            ("Cenário selecionado", "Valorizacao"),
            ("Capital inicial", 100000),
            ("Aporte mensal", 1000),
            ("Retirada mensal planejada", 0),
            ("Data inicial projeções", date(2026, 1, 1)),
            ("Data final projeções", date(2026, 12, 31)),
            ("Renda mensal alvo", 500),
        ],
        start=1,
    ):
        config.cell(row=linha, column=1, value=rotulo)
        config.cell(row=linha, column=2, value=valor)

    posicao = livro.create_sheet("Posicao_B3")
    posicao.append(["Classe", "Ticker", "Ativo", "Qtde", "Preço R$", "Var 12m", "DY 12m", "P/L"])
    posicao.append(["Ações", "PETR4", "Petrobras PN", 100, 38.5, 0.10, 0.08, 5.0])
    posicao.append(["FIIs", "XPML11", "XP Malls", 50, 100.0, -0.02, 0.09, None])
    # Uma fórmula de verdade, para o contador de fórmulas ter o que achar.
    posicao.cell(row=5, column=5, value="=SUM(E2:E3)")

    proventos = livro.create_sheet("Proventos")
    proventos.append(["Data", "Tipo", "Ticker/Código", "Produto", "Valor R$"])
    proventos.append([date(2026, 3, 10), "DIVIDENDO", "PETR4", "PETR4 - Petrobras", 120.0])
    proventos.append([date(2026, 4, 10), "RENDIMENTO", "XPML11", "XPML11 - XP Malls", 80.0])

    cenarios = livro.create_sheet("Cenarios")
    cenarios.append(["Classe", "Peso na carteira B3", "Péssimo", "Ruim", "Otimista", "Muito Favorável"])
    cenarios.append(["Ações", 0.5, -0.10, 0.05, 0.15, 0.25])
    cenarios.append(["FIIs", 0.5, -0.05, 0.06, 0.12, 0.18])
    cenarios.append(["Probabilidade do cenário", None, 0.10, 0.30, 0.40, 0.20])

    memoria = io.BytesIO()
    livro.save(memoria)
    return memoria.getvalue()


@pytest.fixture(scope="module")
def xlsx() -> bytes:
    return _xlsx_completo()


# --------------------------------------------------------------------------
# Pipeline: planilha genérica (só posições)
# --------------------------------------------------------------------------


def test_csv_generico_produz_resultado_util() -> None:
    """Uma tabela de posições basta: carteira, gráficos e KPIs saem preenchidos."""
    analise = pipeline.analyze_bytes(CSV_SIMPLES, "carteira.csv")

    assert analise.portfolio is not None
    assert analise.portfolio.total_value == pytest.approx(100 * 38.5 + 200 * 25.1, rel=1e-9)
    assert analise.portfolio.positions_count == 2
    assert [grafico.id for grafico in analise.charts] == ["alocacao-classe", "top-posicoes"]
    assert [kpi["id"] for kpi in analise.kpis] == ["patrimonio-b3", "ativos"]
    # Sem Config não há plano de aportes: não se projeta o que ninguém informou.
    assert analise.simulation is None and analise.projection is None
    assert any("simulação e projeção não foram geradas" in a for a in analise.warnings)


def test_planilha_vazia_nao_levanta_excecao() -> None:
    analise = pipeline.analyze_bytes(b"", "vazio.csv")
    assert analise.portfolio is not None and analise.portfolio.total_value == 0.0
    assert analise.charts == []
    assert analise.engine["positions"] == 0


def test_metadados_do_motor() -> None:
    analise = pipeline.analyze_bytes(CSV_SIMPLES, "carteira.csv")
    assert set(analise.engine) == {
        "version",
        "elapsed_ms",
        "sheets_read",
        "positions",
        "formulas_replaced",
    }
    assert analise.engine["version"] == pipeline.VERSAO
    assert analise.engine["sheets_read"] == 1
    assert analise.engine["positions"] == 2
    assert analise.engine["formulas_replaced"] == 0  # CSV não tem fórmula
    assert analise.engine["elapsed_ms"] >= 0.0


def test_analise_serializa_em_json() -> None:
    analise = pipeline.analyze_bytes(CSV_SIMPLES, "carteira.csv")
    corpo = json.dumps(to_dict(analise), ensure_ascii=False, allow_nan=False)
    assert '"file_name": "carteira.csv"' in corpo


# --------------------------------------------------------------------------
# Pipeline: planilha completa
# --------------------------------------------------------------------------


def test_xlsx_completo_calcula_todos_os_blocos(xlsx: bytes) -> None:
    analise = pipeline.analyze(xlsx, "sintetica.xlsx")

    assert analise.portfolio is not None
    assert analise.portfolio.total_value == pytest.approx(100 * 38.5 + 50 * 100.0, rel=1e-9)
    assert analise.proventos is not None and analise.proventos.total == pytest.approx(200.0)
    assert analise.scenarios is not None
    assert analise.rebalance is not None
    assert analise.simulation is not None and analise.projection is not None
    assert analise.consolidated is not None
    assert analise.engine["formulas_replaced"] >= 1  # a fórmula plantada na Posicao_B3
    assert analise.period_label.startswith("Proventos de ")


def test_taxa_vem_dos_cenarios_e_alimenta_a_simulacao(xlsx: bytes) -> None:
    """Retorno esperado = Σ(retorno do cenário × probabilidade)."""
    analise = pipeline.analyze(xlsx, "sintetica.xlsx")
    esperado = (
        0.10 * (0.5 * -0.10 + 0.5 * -0.05)
        + 0.30 * (0.5 * 0.05 + 0.5 * 0.06)
        + 0.40 * (0.5 * 0.15 + 0.5 * 0.12)
        + 0.20 * (0.5 * 0.25 + 0.5 * 0.18)
    )
    assert analise.scenarios is not None
    assert analise.scenarios.expected_return == pytest.approx(esperado, rel=1e-9)
    assert analise.simulation is not None
    assert analise.simulation.annual_rate == pytest.approx(esperado, rel=1e-9)
    kpi = next(k for k in analise.kpis if k["id"] == "retorno-esperado")
    assert kpi["value"] == pytest.approx(esperado, rel=1e-9)
    assert "Cenarios" in kpi["hint"]


def test_taxa_cai_para_o_config_sem_probabilidades() -> None:
    """Sem cenários válidos vale o retorno derivado do Config, pelo regime escolhido."""
    from engine.calc.projecao import annual_returns

    assumptions = Assumptions(scenario="Renda", initial_capital=1000.0)
    renda, valorizacao = annual_returns(assumptions)

    taxa, origem = pipeline._taxa_anual(None, assumptions, tem_config=True)
    assert origem == "assumptions" and taxa == pytest.approx(renda, rel=1e-12)

    taxa, _ = pipeline._taxa_anual(None, Assumptions(scenario="Valorizacao"), tem_config=True)
    assert taxa == pytest.approx(valorizacao, rel=1e-12)


def test_sem_config_a_taxa_nao_e_inventada() -> None:
    """Planilha sem Config e sem Cenarios não ganha uma previsão de brinde."""
    taxa, origem = pipeline._taxa_anual(None, Assumptions(), tem_config=False)
    assert taxa == 0.0 and origem == "indisponivel"


def test_cenarios_com_probabilidade_incompleta_cai_no_config() -> None:
    vazio = ScenarioResult(
        classes=[],
        returns_by_scenario={},
        probabilities={},
        expected_return=0.0,
        inflation_average=0.0,
        selic_average=0.0,
        weighted_dy_stocks=0.0,
        weighted_dy_fii=0.0,
        weighted_pe_stocks=0.0,
        weighted_pb_fii=0.0,
        inflation_forecast=[],
    )
    _, origem = pipeline._taxa_anual(vazio, Assumptions(), tem_config=True)
    assert origem == "assumptions"


# --------------------------------------------------------------------------
# Pipeline: isolamento de falhas
# --------------------------------------------------------------------------


def test_bloco_que_falha_vira_none_com_aviso(monkeypatch: pytest.MonkeyPatch) -> None:
    """Um módulo quebrado não pode custar a análise inteira."""

    def explodir(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("defeito proposital")

    monkeypatch.setattr(pipeline, "compute_portfolio", explodir)
    analise = pipeline.analyze_bytes(CSV_SIMPLES, "carteira.csv")

    assert analise.portfolio is None
    assert analise.proventos is not None  # o resto continua
    assert any(
        "Não foi possível calcular a carteira" in aviso and "RuntimeError" in aviso
        for aviso in analise.warnings
    )


def test_avisos_nao_se_repetem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pipeline.extract,
        "extract_all",
        lambda book: _entradas_com_avisos(["mesmo aviso", "mesmo aviso", "outro"]),
    )
    analise = pipeline.analyze_bytes(CSV_SIMPLES, "carteira.csv")
    assert analise.warnings.count("mesmo aviso") == 1
    assert "outro" in analise.warnings


def _entradas_com_avisos(avisos: List[str]):
    from engine.models import WorkbookInputs

    return WorkbookInputs(warnings=list(avisos))


# --------------------------------------------------------------------------
# Pipeline: cotações
# --------------------------------------------------------------------------


def test_refresh_quotes_atualiza_precos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        quotes,
        "fetch_quotes",
        lambda tickers, token=None, timeout=8.0: {
            "PETR4": quotes.Quote(ticker="PETR4", price=40.0, change_percent=0.01)
        },
    )
    analise = pipeline.analyze_bytes(CSV_SIMPLES, "carteira.csv", refresh_quotes=True)

    assert analise.portfolio is not None
    assert analise.portfolio.total_value == pytest.approx(100 * 40.0 + 200 * 25.1, rel=1e-9)
    assert any("Cotações atualizadas" in aviso for aviso in analise.warnings)
    assert any("BBAS3" in aviso for aviso in analise.warnings)  # ficou sem cotação


def test_refresh_quotes_sem_rede_mantem_a_planilha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quotes, "fetch_quotes", lambda *a, **k: {})
    analise = pipeline.analyze_bytes(CSV_SIMPLES, "carteira.csv", refresh_quotes=True)

    assert analise.portfolio is not None
    assert analise.portfolio.total_value == pytest.approx(100 * 38.5 + 200 * 25.1, rel=1e-9)
    assert any("não foi possível atualizar as cotações" in a.lower() for a in analise.warnings)


def test_refresh_quotes_respeita_precos_travados(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tesouro Direto nunca é cotado, mesmo que a API devolva um homônimo."""
    csv = "Classe;Ticker;Quantidade;Preco\r\nTesouro Direto;LFT;1;14000,00\r\n".encode("utf-8")
    chamadas: List[Any] = []

    def espiao(tickers: Any, token: Any = None, timeout: float = 8.0) -> Dict[str, Any]:
        chamadas.append(list(tickers))
        return {"LFT": quotes.Quote(ticker="LFT", price=1.0)}

    monkeypatch.setattr(quotes, "fetch_quotes", espiao)
    analise = pipeline.analyze_bytes(csv, "rf.csv", refresh_quotes=True)

    assert chamadas == []  # nem chegou a pedir cotação
    assert analise.portfolio is not None
    assert analise.portfolio.total_value == pytest.approx(14000.0)


def test_falha_de_cotacao_nao_derruba_a_analise(monkeypatch: pytest.MonkeyPatch) -> None:
    def explodir(*args: Any, **kwargs: Any) -> Any:
        raise OSError("rede fora")

    monkeypatch.setattr(quotes, "fetch_quotes", explodir)
    analise = pipeline.analyze_bytes(CSV_SIMPLES, "carteira.csv", refresh_quotes=True)
    assert analise.portfolio is not None
    assert any("cotações" in aviso.lower() for aviso in analise.warnings)


# --------------------------------------------------------------------------
# Pipeline: auxiliares
# --------------------------------------------------------------------------


def test_contar_formulas(xlsx: bytes) -> None:
    assert pipeline.contar_formulas(xlsx, "sintetica.xlsx") >= 1
    assert pipeline.contar_formulas(CSV_SIMPLES, "carteira.csv") == 0
    assert pipeline.contar_formulas(b"nao e zip", "quebrado.xlsx") == 0
    assert pipeline.contar_formulas(io.BytesIO(xlsx), "sintetica.xlsx") >= 1


def test_formatadores_pt_br() -> None:
    assert pipeline._moeda(1234.5) == "R$ 1.234,50"
    assert pipeline._moeda(-1234.5) == "R$ -1.234,50"
    assert pipeline._percentual(0.1120711) == "11,21%"
    assert pipeline._mes_pt(date(2026, 7, 1)) == "jul/2026"
    assert pipeline._plural(1, "posição", "posições") == "1 posição"
    assert pipeline._plural(0, "posição", "posições") == "0 posições"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_resumo_traz_as_secoes(xlsx: bytes) -> None:
    analise = pipeline.analyze(xlsx, "sintetica.xlsx")
    texto = cli.formatar_resumo(analise)
    assert "INDICADORES" in texto
    assert "TOP 10 POSIÇÕES" in texto
    assert "REBALANCEAMENTO" in texto
    assert "PETR4" in texto
    assert "R$ " in texto


def test_formatar_valores() -> None:
    assert cli.formatar(1234.5) == "R$ 1.234,50"
    assert cli.formatar(0.1234, "percent") == "12,34%"
    assert cli.formatar(78, "number") == "78"
    assert cli.formatar(None) == "R$ 0,00"
    assert cli.formatar("já formatado") == "já formatado"


def test_cli_grava_json(tmp_path: Path) -> None:
    entrada = tmp_path / "carteira.csv"
    entrada.write_bytes(CSV_SIMPLES)
    saida = tmp_path / "analise.json"

    assert cli.main([str(entrada), "--json", str(saida)]) == 0

    payload = json.loads(saida.read_text(encoding="utf-8"))
    assert payload["portfolio"]["positions_count"] == 2
    assert payload["engine"]["version"] == pipeline.VERSAO


def test_cli_arquivo_inexistente(tmp_path: Path) -> None:
    assert cli.main([str(tmp_path / "nao-existe.xlsx")]) == 1


# --------------------------------------------------------------------------
# Servidor de desenvolvimento
# --------------------------------------------------------------------------


def test_rota_normalizada() -> None:
    assert server._rota("/planilha?x=1") == "/planilha"
    assert server._rota("/api/py/planilha/") == "/api/py/planilha"
    assert server._rota("") == "/"


def test_cors_liberado_so_para_localhost() -> None:
    local = server._cabecalhos_cors("http://localhost:3000")
    assert local["Access-Control-Allow-Origin"] == "http://localhost:3000"

    outra_porta = server._cabecalhos_cors("http://127.0.0.1:5173")
    assert outra_porta["Access-Control-Allow-Origin"] == "http://127.0.0.1:5173"

    externo = server._cabecalhos_cors("https://exemplo.invalid")
    assert "Access-Control-Allow-Origin" not in externo

    sem_origem = server._cabecalhos_cors("")
    assert sem_origem["Access-Control-Allow-Origin"] == "*"


def test_carrega_o_parser_da_api() -> None:
    api = server.carregar_api()
    assert hasattr(api, "processar") and hasattr(api, "corpo_json")
    assert api.LIMITE_BYTES > 0


@pytest.fixture()
def servidor_local():
    """Sobe o servidor numa porta livre e o derruba no fim do teste."""
    servidor = server.criar_servidor("127.0.0.1", 0)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    host, porta = servidor.server_address[:2]
    try:
        yield f"http://{host}:{porta}"
    finally:
        servidor.shutdown()
        servidor.server_close()
        thread.join(timeout=5)


def test_health_responde(servidor_local: str) -> None:
    with urllib.request.urlopen(f"{servidor_local}/health", timeout=10) as resposta:
        assert resposta.status == 200
        payload = json.loads(resposta.read())
    assert payload["status"] == "ok"
    assert payload["engine"] == pipeline.VERSAO


def test_post_planilha_analisa(servidor_local: str) -> None:
    corpo = json.dumps(
        {
            "file_base64": base64.b64encode(CSV_SIMPLES).decode(),
            "file_name": "carteira.csv",
        }
    ).encode("utf-8")
    pedido = urllib.request.Request(
        f"{servidor_local}/api/py/planilha",
        data=corpo,
        headers={"Content-Type": "application/json", "Origin": "http://localhost:3000"},
    )
    with urllib.request.urlopen(pedido, timeout=30) as resposta:
        assert resposta.status == 200
        assert resposta.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"
        payload = json.loads(resposta.read())
    assert payload["portfolio"]["positions_count"] == 2
    assert len(payload["kpis"]) >= 1


def test_rota_desconhecida(servidor_local: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as erro:
        urllib.request.urlopen(f"{servidor_local}/nao-existe", timeout=10)
    assert erro.value.code == 404
    assert "Rota desconhecida" in json.loads(erro.value.read())["error"]
