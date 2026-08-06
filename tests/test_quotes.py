"""Testes de ``engine.quotes`` — a rede é sempre falsa (nenhum acesso real)."""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import quotes  # noqa: E402
from engine.models import Position  # noqa: E402
from engine.quotes import (  # noqa: E402
    Quote,
    apply_quotes,
    fetch_quotes,
    tickers_atualizaveis,
)

REL = 1e-9


# --------------------------------------------------------------------------
# Transporte falso
# --------------------------------------------------------------------------


class _RespostaFalsa:
    """Imita o objeto devolvido por ``urlopen`` (context manager + read)."""

    def __init__(self, corpo: bytes, status: int = 200) -> None:
        self._corpo = corpo
        self.status = status

    def read(self, tamanho: Optional[int] = None) -> bytes:
        return self._corpo

    def __enter__(self) -> "_RespostaFalsa":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


def _corpo(*ativos: Dict[str, Any]) -> bytes:
    return json.dumps({"results": list(ativos)}).encode("utf-8")


def _ativo(simbolo: str, preco: float = 10.0, variacao: float = 1.25) -> Dict[str, Any]:
    return {
        "symbol": simbolo,
        "shortName": f"{simbolo} ON",
        "regularMarketPrice": preco,
        "regularMarketChangePercent": variacao,
        "currency": "BRL",
    }


def _instalar(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[str], Any],
) -> List[Tuple[str, Dict[str, str], Optional[float]]]:
    """Substitui ``urlopen`` e devolve a lista de chamadas (url, headers, timeout)."""
    chamadas: List[Tuple[str, Dict[str, str], Optional[float]]] = []

    def _falso_urlopen(pedido: Any, timeout: Optional[float] = None) -> Any:
        url = pedido.full_url if hasattr(pedido, "full_url") else str(pedido)
        cabecalhos = dict(getattr(pedido, "headers", {}) or {})
        chamadas.append((url, cabecalhos, timeout))
        resultado = responder(url)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    monkeypatch.setattr(quotes.urllib.request, "urlopen", _falso_urlopen)
    return chamadas


def _tickers_da_url(url: str) -> List[str]:
    caminho = urllib.parse.urlsplit(url).path
    return urllib.parse.unquote(caminho.rsplit("/", 1)[-1]).split(",")


# --------------------------------------------------------------------------
# fetch_quotes — requisição em lote
# --------------------------------------------------------------------------


def test_uma_unica_requisicao_para_varios_tickers(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas = _instalar(
        monkeypatch,
        lambda url: _RespostaFalsa(
            _corpo(_ativo("PETR4", 38.5), _ativo("BBAS3", 25.0), _ativo("XPML11", 100.0))
        ),
    )
    cotacoes = fetch_quotes(["PETR4", "BBAS3", "XPML11"])

    assert len(chamadas) == 1  # o VBA fazia uma chamada por ticker
    url, cabecalhos, timeout = chamadas[0]
    assert url.startswith("https://brapi.dev/api/quote/")
    assert _tickers_da_url(url) == ["PETR4", "BBAS3", "XPML11"]
    assert timeout == pytest.approx(8.0, rel=REL)
    assert "Authorization" not in {chave.title() for chave in cabecalhos}
    assert "token=" not in url
    assert set(cotacoes) == {"PETR4", "BBAS3", "XPML11"}
    assert cotacoes["PETR4"].price == pytest.approx(38.5, rel=REL)
    assert cotacoes["PETR4"].short_name == "PETR4 ON"
    assert cotacoes["PETR4"].source == "brapi.dev"


def test_variacao_do_dia_vira_fracao(monkeypatch: pytest.MonkeyPatch) -> None:
    _instalar(
        monkeypatch,
        lambda url: _RespostaFalsa(
            _corpo(
                _ativo("PETR4", 38.5, variacao=1.25),
                {"symbol": "BBAS3", "regularMarketPrice": 25.0},
            )
        ),
    )
    cotacoes = fetch_quotes(["PETR4", "BBAS3"])
    assert cotacoes["PETR4"].change_percent == pytest.approx(0.0125, rel=REL)
    assert cotacoes["BBAS3"].change_percent is None
    assert cotacoes["BBAS3"].short_name == "BBAS3"  # cai para o próprio símbolo


def test_json_de_verdade_e_nao_busca_de_substring(monkeypatch: pytest.MonkeyPatch) -> None:
    """O VBA lia por ``InStr``; um campo textual parecido o enganava."""
    carga = {
        "results": [
            {
                "logourl": "https://cdn/regularMarketPrice=999.99.png",
                "longName": "Petróleo Brasileiro S.A.",
                "regularMarketChangePercent": -2.5,
                "regularMarketPrice": 38.42,
                "symbol": "petr4",
            }
        ],
        "requestedAt": "2026-08-05T12:00:00.000Z",
    }
    _instalar(monkeypatch, lambda url: _RespostaFalsa(json.dumps(carga).encode("utf-8")))
    cotacoes = fetch_quotes(["PETR4"])
    assert cotacoes["PETR4"].price == pytest.approx(38.42, rel=REL)
    assert cotacoes["PETR4"].change_percent == pytest.approx(-0.025, rel=REL)
    assert cotacoes["PETR4"].short_name == "Petróleo Brasileiro S.A."


def test_tickers_normalizados_e_sem_repeticao(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas = _instalar(monkeypatch, lambda url: _RespostaFalsa(_corpo(_ativo("PETR4"))))
    cotacoes = fetch_quotes([" petr4 ", "PETR4", "", None, "petr4"])  # type: ignore[list-item]
    assert _tickers_da_url(chamadas[0][0]) == ["PETR4"]
    assert list(cotacoes) == ["PETR4"]


def test_lotes_de_vinte_tickers(monkeypatch: pytest.MonkeyPatch) -> None:
    tickers = [f"AAA{indice:02d}" for indice in range(45)]

    def responder(url: str) -> _RespostaFalsa:
        return _RespostaFalsa(_corpo(*[_ativo(t) for t in _tickers_da_url(url)]))

    chamadas = _instalar(monkeypatch, responder)
    cotacoes = fetch_quotes(tickers)

    assert [len(_tickers_da_url(url)) for url, _, _ in chamadas] == [20, 20, 5]
    assert len(cotacoes) == 45
    assert _tickers_da_url(chamadas[0][0])[0] == "AAA00"
    assert _tickers_da_url(chamadas[2][0])[-1] == "AAA44"


def test_um_lote_que_falha_nao_derruba_os_outros(monkeypatch: pytest.MonkeyPatch) -> None:
    tickers = [f"AAA{indice:02d}" for indice in range(25)]
    primeiro_lote = set(tickers[:20])

    def responder(url: str) -> Any:
        lote = set(_tickers_da_url(url))
        if lote <= primeiro_lote:
            return urllib.error.URLError("sem rede")
        return _RespostaFalsa(_corpo(*[_ativo(t) for t in sorted(lote)]))

    chamadas = _instalar(monkeypatch, responder)
    cotacoes = fetch_quotes(tickers)
    assert set(cotacoes) == {f"AAA{indice:02d}" for indice in range(20, 25)}
    # 1 lote quebrado + 3 tentativas individuais (freio) + 1 lote bom.
    assert len(chamadas) == 5


def test_sem_token_cai_para_uma_requisicao_por_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plano gratuito da brapi: lote devolve 401, ticker a ticker funciona."""

    def responder(url: str) -> Any:
        lote = _tickers_da_url(url)
        if len(lote) > 1:
            return urllib.error.HTTPError(url, 401, "MISSING_TOKEN", None, None)  # type: ignore[arg-type]
        return _RespostaFalsa(_corpo(_ativo(lote[0], 20.0)))

    chamadas = _instalar(monkeypatch, responder)
    cotacoes = fetch_quotes(["PETR4", "BBAS3", "XPML11"])

    assert set(cotacoes) == {"PETR4", "BBAS3", "XPML11"}
    assert len(chamadas) == 4  # 1 lote recusado + 3 individuais
    assert cotacoes["BBAS3"].price == pytest.approx(20.0, rel=REL)


def test_resgate_individual_para_depois_de_tres_falhas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rede fora não pode virar uma requisição por ativo da carteira."""
    chamadas = _instalar(monkeypatch, lambda url: urllib.error.URLError("sem rede"))
    assert fetch_quotes([f"AAA{indice:02d}" for indice in range(45)]) == {}
    # 3 lotes + apenas 3 tentativas individuais antes do freio.
    assert len(chamadas) == 6


def test_lote_bem_sucedido_rearma_o_resgate_individual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Um lote que responde prova que a rede voltou — o freio precisa soltar.

    Sem o rearme, as 3 falhas do primeiro lote desligariam o resgate ticker a
    ticker para sempre, e o terceiro lote (que só funciona individualmente)
    voltaria vazio mesmo com a rede sabidamente de pé.
    """
    tickers = [f"AAA{indice:02d}" for indice in range(60)]
    primeiro, segundo, terceiro = (set(tickers[:20]), set(tickers[20:40]), set(tickers[40:]))

    def responder(url: str) -> Any:
        lote = set(_tickers_da_url(url))
        if lote <= primeiro:  # rede fora: lote e individuais falham
            return urllib.error.URLError("sem rede")
        if lote <= segundo:  # rede voltou
            return _RespostaFalsa(_corpo(*[_ativo(t) for t in sorted(lote)]))
        if len(lote) > 1:  # terceiro lote: só responde ticker a ticker
            return urllib.error.HTTPError(url, 401, "MISSING_TOKEN", None, None)  # type: ignore[arg-type]
        return _RespostaFalsa(_corpo(_ativo(sorted(lote)[0])))

    _instalar(monkeypatch, responder)
    cotacoes = fetch_quotes(tickers)

    assert segundo <= set(cotacoes)
    assert terceiro <= set(cotacoes)
    assert not (set(cotacoes) & primeiro)


# --------------------------------------------------------------------------
# fetch_quotes — token
# --------------------------------------------------------------------------


def test_token_vai_no_header_e_na_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAPI_TOKEN", raising=False)
    chamadas = _instalar(monkeypatch, lambda url: _RespostaFalsa(_corpo(_ativo("PETR4"))))
    fetch_quotes(["PETR4"], token="tok en/123")

    url, cabecalhos, _ = chamadas[0]
    normalizados = {chave.title(): valor for chave, valor in cabecalhos.items()}
    assert normalizados["Authorization"] == "Bearer tok en/123"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["token"] == ["tok en/123"]


def test_token_vem_do_ambiente_quando_nao_informado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAPI_TOKEN", "do-ambiente")
    chamadas = _instalar(monkeypatch, lambda url: _RespostaFalsa(_corpo(_ativo("PETR4"))))
    fetch_quotes(["PETR4"])
    url, cabecalhos, _ = chamadas[0]
    assert {c.title(): v for c, v in cabecalhos.items()}["Authorization"] == "Bearer do-ambiente"
    assert "token=do-ambiente" in url


def test_token_explicito_vence_o_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAPI_TOKEN", "do-ambiente")
    chamadas = _instalar(monkeypatch, lambda url: _RespostaFalsa(_corpo(_ativo("PETR4"))))
    fetch_quotes(["PETR4"], token="explicito")
    assert "token=explicito" in chamadas[0][0]


# --------------------------------------------------------------------------
# fetch_quotes — falhas nunca levantam exceção
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resposta",
    [
        urllib.error.URLError("dns"),
        urllib.error.HTTPError("https://brapi.dev", 401, "não autorizado", None, None),  # type: ignore[arg-type]
        urllib.error.HTTPError("https://brapi.dev", 429, "limite", None, None),  # type: ignore[arg-type]
        socket.timeout("tempo esgotado"),
        ValueError("erro inesperado do transporte"),
        _RespostaFalsa(b"", status=500),
        _RespostaFalsa(b"nao e json"),
        _RespostaFalsa(b""),
        _RespostaFalsa(b"[1, 2, 3]"),
        _RespostaFalsa(b'{"error": true, "message": "ticker invalido"}'),
        _RespostaFalsa(b'{"results": null}'),
        _RespostaFalsa(b'{"results": "PETR4"}'),
        _RespostaFalsa(b'{"results": [null, 7]}'),
    ],
)
def test_qualquer_falha_devolve_dicionario_vazio(
    monkeypatch: pytest.MonkeyPatch, resposta: Any
) -> None:
    _instalar(monkeypatch, lambda url: resposta)
    assert fetch_quotes(["PETR4", "BBAS3"]) == {}


def test_nunca_inventa_preco(monkeypatch: pytest.MonkeyPatch) -> None:
    _instalar(
        monkeypatch,
        lambda url: _RespostaFalsa(
            _corpo(
                {"symbol": "SEMPRECO", "shortName": "Sem preço"},
                {"symbol": "ZERADO", "regularMarketPrice": 0},
                {"symbol": "NULO", "regularMarketPrice": None},
                {"symbol": "TEXTO", "regularMarketPrice": "abc"},
                {"symbol": "", "regularMarketPrice": 10.0},
                {"symbol": "NEGATIVO", "regularMarketPrice": -5.0},
                {"symbol": "OK11", "regularMarketPrice": "12,50"},
            )
        ),
    )
    cotacoes = fetch_quotes(["SEMPRECO", "ZERADO", "NULO", "TEXTO", "NEGATIVO", "OK11"])
    assert list(cotacoes) == ["OK11"]
    assert cotacoes["OK11"].price == pytest.approx(12.5, rel=REL)


def test_sem_tickers_nao_toca_a_rede(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explodir(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - não deve rodar
        raise AssertionError("fetch_quotes não pode acessar a rede sem tickers")

    monkeypatch.setattr(quotes.urllib.request, "urlopen", _explodir)
    assert fetch_quotes([]) == {}
    assert fetch_quotes(["", "   "]) == {}
    assert fetch_quotes(None) == {}  # type: ignore[arg-type]


def test_timeout_invalido_cai_no_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas = _instalar(monkeypatch, lambda url: _RespostaFalsa(_corpo(_ativo("PETR4"))))
    fetch_quotes(["PETR4"], timeout=0)
    fetch_quotes(["PETR4"], timeout=None)  # type: ignore[arg-type]
    fetch_quotes(["PETR4"], timeout=2.5)
    assert [timeout for _, _, timeout in chamadas] == [8.0, 8.0, 2.5]


# --------------------------------------------------------------------------
# apply_quotes
# --------------------------------------------------------------------------


def _posicoes() -> List[Position]:
    return [
        Position("Ações", "PETR4", "Petrobras PN", 100.0, 30.0),
        Position("FIIs", "XPML11", "XP Malls", 50.0, 100.0),
        Position("FIIs", "PATL11", "Pátria Log (resíduo)", 1.0, 0.01, note="Não atualizar"),
        Position("Renda Fixa privada", "CDB01", "CDB XP", 1.0, 1_000.0),
        Position("Tesouro Direto", "", "Tesouro IPCA+ 2050", 1.0, 4_000.0),
        Position("Ações", "SUMIU3", "Empresa sem cotação", 10.0, 7.0),
    ]


def _cotacoes() -> Dict[str, Quote]:
    return {
        "PETR4": Quote("PETR4", 38.5, 0.01, "Petrobras", "brapi.dev"),
        "XPML11": Quote("XPML11", 110.0, -0.005, "XP Malls", "brapi.dev"),
        "PATL11": Quote("PATL11", 55.0, 0.0, "Pátria Log", "brapi.dev"),
        "CDB01": Quote("CDB01", 12.0, 0.0, "Homônimo", "brapi.dev"),
    }


def test_aplica_precos_e_preserva_os_travados() -> None:
    originais = _posicoes()
    novas, avisos = apply_quotes(originais, _cotacoes())

    por_ticker = {posicao.ticker or posicao.name: posicao for posicao in novas}
    assert por_ticker["PETR4"].price == pytest.approx(38.5, rel=REL)
    assert por_ticker["XPML11"].price == pytest.approx(110.0, rel=REL)
    # Travados: resíduo marcado "não atualizar", Renda Fixa e Tesouro.
    assert por_ticker["PATL11"].price == pytest.approx(0.01, rel=REL)
    assert por_ticker["CDB01"].price == pytest.approx(1_000.0, rel=REL)
    assert por_ticker["Tesouro IPCA+ 2050"].price == pytest.approx(4_000.0, rel=REL)
    # Sem cotação: preço da planilha mantido + aviso.
    assert por_ticker["SUMIU3"].price == pytest.approx(7.0, rel=REL)
    assert avisos == ["Cotação não encontrada para SUMIU3 — preço da planilha mantido."]

    # A lista original não é modificada (Position é frozen).
    assert [posicao.price for posicao in originais] == [30.0, 100.0, 0.01, 1_000.0, 4_000.0, 7.0]
    assert len(novas) == len(originais)


def test_quantidade_e_demais_campos_intactos() -> None:
    novas, _ = apply_quotes(_posicoes(), _cotacoes())
    petr = novas[0]
    assert petr.ticker == "PETR4"
    assert petr.quantity == pytest.approx(100.0, rel=REL)
    assert petr.market_value == pytest.approx(3_850.0, rel=REL)
    assert petr.name == "Petrobras PN"


def test_ticker_case_insensitive_e_chaves_sujas() -> None:
    posicoes = [Position("Ações", " petr4 ", "Petrobras", 10.0, 1.0)]
    novas, avisos = apply_quotes(posicoes, {" petr4 ": Quote("PETR4", 40.0, None, "P", "brapi.dev")})
    assert novas[0].price == pytest.approx(40.0, rel=REL)
    assert avisos == []


def test_cotacao_sem_preco_valido_nao_e_aplicada() -> None:
    posicoes = [Position("Ações", "PETR4", "Petrobras", 10.0, 30.0)]
    novas, avisos = apply_quotes(posicoes, {"PETR4": Quote("PETR4", 0.0, None, "P", "brapi.dev")})
    assert novas[0].price == pytest.approx(30.0, rel=REL)
    assert avisos == ["Cotação não encontrada para PETR4 — preço da planilha mantido."]


def test_avisos_sem_repetir_o_mesmo_ticker() -> None:
    posicoes = [
        Position("Ações", "SUMIU3", "A", 1.0, 1.0),
        Position("Ações", "sumiu3", "B", 2.0, 2.0),
        Position("FIIs", "OUTRO11", "C", 3.0, 3.0),
    ]
    _, avisos = apply_quotes(posicoes, {})
    assert avisos == [
        "Cotação não encontrada para SUMIU3 — preço da planilha mantido.",
        "Cotação não encontrada para OUTRO11 — preço da planilha mantido.",
    ]


def test_entradas_vazias_ou_nulas_nao_quebram() -> None:
    assert apply_quotes([], {}) == ([], [])
    assert apply_quotes(None, None) == ([], [])  # type: ignore[arg-type]
    novas, avisos = apply_quotes([None, Position("Ações", "", "Sem código", 1.0, 5.0)], None)  # type: ignore[list-item]
    assert len(novas) == 1  # posição sem ticker é preservada, sem aviso
    assert avisos == []
    assert apply_quotes(_posicoes(), {"PETR4": None})[1][0].startswith("Cotação não encontrada")  # type: ignore[dict-item]


def test_tickers_atualizaveis_ignora_travados() -> None:
    assert tickers_atualizaveis(_posicoes()) == ["PETR4", "XPML11", "SUMIU3"]
    assert tickers_atualizaveis([]) == []
    assert tickers_atualizaveis(None) == []
