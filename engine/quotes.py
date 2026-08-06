"""Cotações da B3 pela API pública da brapi.dev.

Substitui a macro VBA ``AtualizarCotacoes`` da planilha, que:

* só funcionava no macOS (chamava ``AppleScriptTask`` com um ``.scpt`` externo);
* fazia **uma requisição por ticker** (78 chamadas para atualizar a carteira);
* "parseava" o JSON procurando substrings (``InStr``/``Mid``), o que devolvia
  preço errado sempre que a API mudava a ordem dos campos.

Aqui a conversa com a brapi é direta, via ``urllib`` da stdlib: **uma
requisição em lote** para ``/api/quote/TICKER1,TICKER2,...``, ``json.loads`` de
verdade e nenhum preço inventado — o que a API não devolver simplesmente não
entra no dicionário de retorno.

Regras de robustez (o motor roda em cima de planilha de usuário):

* ``fetch_quotes`` **nunca** levanta exceção. Erro de rede, HTTP 4xx/5xx, JSON
  inválido ou resposta fora do formato viram dicionário vazio (ou o resultado
  parcial dos lotes que deram certo).
* Um lote que falha não derruba os outros e, como o plano gratuito da brapi
  recusa lotes (``401 MISSING_TOKEN``) mas atende um ticker por vez, o lote
  vazio é retentado ticker a ticker — com freio, para não virar a enxurrada
  de requisições do VBA quando a rede está fora.
* ``apply_quotes`` preserva o preço das posições com ``price_locked``
  (resíduo marcado "não atualizar", Renda Fixa privada e Tesouro Direto).

Este é o único módulo do motor que fala com a rede; tudo em ``engine.calc``
continua puro.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .models import Position

#: Endpoint de cotações da brapi (o ticker vai concatenado no caminho).
BASE_URL = "https://brapi.dev/api/quote/"
#: Tickers por requisição — a brapi limita o tamanho do lote.
TAMANHO_LOTE = 20
#: Identificação da fonte, gravada em cada :class:`Quote`.
FONTE = "brapi.dev"
#: Teto de leitura da resposta (uma carteira inteira não passa disso).
_LIMITE_RESPOSTA = 4 * 1024 * 1024
_TIMEOUT_PADRAO = 8.0
_USER_AGENT = "celestia-financeiro/1.0 (+engine.quotes)"
#: Falhas individuais seguidas que desligam o resgate ticker a ticker.
#: Sem esse freio, uma rede fora custaria uma requisição por ativo da carteira.
_FALHAS_SEGUIDAS_MAX = 3


@dataclass(frozen=True)
class Quote:
    """Cotação de um ativo.

    ``change_percent`` é **fração** (0,0123 = +1,23%), como todo percentual do
    motor (``var_12m``, ``dividend_yield``, pesos). A brapi devolve pontos
    percentuais, então o valor é dividido por 100 na leitura.
    """

    ticker: str
    price: float
    change_percent: Optional[float] = None
    short_name: str = ""
    source: str = FONTE


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def _numero(valor: Any) -> Optional[float]:
    """``float`` de um campo da API; ``None`` para ausente/inválido/NaN."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, str):
        texto = valor.strip().replace(",", ".")
        if not texto:
            return None
        valor = texto
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if numero != numero or numero in (float("inf"), float("-inf")):
        return None
    return numero


def _normalizar_ticker(valor: Any) -> str:
    return _texto(valor).upper()


def _tickers_unicos(tickers: Optional[Sequence[str]]) -> List[str]:
    """Maiúsculas, sem espaços, sem vazios e sem repetições (ordem preservada)."""
    vistos: set[str] = set()
    limpos: List[str] = []
    for bruto in tickers or ():
        ticker = _normalizar_ticker(bruto)
        if not ticker or ticker in vistos:
            continue
        vistos.add(ticker)
        limpos.append(ticker)
    return limpos


def _lotes(tickers: Sequence[str], tamanho: int = TAMANHO_LOTE) -> Iterator[List[str]]:
    passo = tamanho if tamanho > 0 else TAMANHO_LOTE
    for inicio in range(0, len(tickers), passo):
        yield list(tickers[inicio : inicio + passo])


def _token(token: Optional[str]) -> str:
    """Token explícito ou ``BRAPI_TOKEN`` do ambiente (vazio = API pública)."""
    explicito = _texto(token)
    if explicito:
        return explicito
    try:
        return _texto(os.environ.get("BRAPI_TOKEN"))
    except Exception:  # pragma: no cover - ambiente sem os.environ utilizável
        return ""


def _timeout(timeout: Any) -> float:
    numero = _numero(timeout)
    if numero is None or numero <= 0:
        return _TIMEOUT_PADRAO
    return numero


def _url(lote: Sequence[str], token: str) -> str:
    """URL do lote; o token também vai na query (fallback do header)."""
    caminho = urllib.parse.quote(",".join(lote), safe=",")
    url = f"{BASE_URL}{caminho}"
    if token:
        url = f"{url}?{urllib.parse.urlencode({'token': token})}"
    return url


# --------------------------------------------------------------------------
# Transporte (único ponto que toca a rede — os testes o substituem)
# --------------------------------------------------------------------------


def _baixar(url: str, token: str, timeout: float) -> Optional[bytes]:
    """GET simples; devolve ``None`` em qualquer falha, sem propagar exceção."""
    cabecalhos = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    pedido = urllib.request.Request(url, headers=cabecalhos, method="GET")
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            status = getattr(resposta, "status", None)
            if status is None:
                status = getattr(resposta, "code", 200)
            if int(status) != 200:
                return None
            return resposta.read(_LIMITE_RESPOSTA)
    except Exception:
        # Rede fora, DNS, TLS, timeout, HTTP 401/404/429/5xx: sem cotação.
        # Preço da planilha é preferível a preço inventado.
        return None


def _interpretar(bruto: Optional[bytes]) -> Dict[str, Quote]:
    """JSON da brapi -> ``{TICKER: Quote}``; ``{}`` se a resposta não servir."""
    if not bruto:
        return {}
    try:
        if isinstance(bruto, (bytes, bytearray)):
            bruto = bytes(bruto).decode("utf-8", errors="replace")
        carga = json.loads(bruto)
    except Exception:
        return {}
    if not isinstance(carga, dict):
        return {}
    resultados = carga.get("results")
    if not isinstance(resultados, list):
        return {}

    cotacoes: Dict[str, Quote] = {}
    for item in resultados:
        if not isinstance(item, dict):
            continue
        ticker = _normalizar_ticker(item.get("symbol") or item.get("ticker"))
        preco = _numero(item.get("regularMarketPrice"))
        if not ticker or preco is None or preco <= 0:
            continue  # sem preço confiável, não inventamos nada
        variacao = _numero(item.get("regularMarketChangePercent"))
        cotacoes[ticker] = Quote(
            ticker=ticker,
            price=preco,
            change_percent=None if variacao is None else variacao / 100.0,
            short_name=_texto(item.get("shortName") or item.get("longName")) or ticker,
            source=FONTE,
        )
    return cotacoes


# --------------------------------------------------------------------------
# Entradas públicas
# --------------------------------------------------------------------------


def fetch_quotes(
    tickers: Sequence[str],
    token: Optional[str] = None,
    timeout: float = 8.0,
) -> Dict[str, Quote]:
    """Busca as cotações na brapi em lotes de :data:`TAMANHO_LOTE` tickers.

    ``token`` vem por parâmetro ou da variável de ambiente ``BRAPI_TOKEN``;
    quando existe, é enviado no header ``Authorization: Bearer`` **e** na query
    (a brapi aceita os dois, e planos antigos só entendem a query).

    Devolve ``{TICKER: Quote}`` só com o que a API confirmou — lista vazia,
    rede fora ou JSON inválido devolvem ``{}``, nunca exceção.

    Quando um lote não volta com nada, os tickers dele são tentados um a um:
    o plano gratuito da brapi responde ``401 MISSING_TOKEN`` para requisições
    com mais de um ticker, mas atende ticker a ticker. Esse resgate para
    sozinho depois de :data:`_FALHAS_SEGUIDAS_MAX` falhas seguidas — se a rede
    (e não o plano) é o problema, não adianta insistir 78 vezes.
    """
    simbolos = _tickers_unicos(tickers)
    if not simbolos:
        return {}
    chave = _token(token)
    espera = _timeout(timeout)

    cotacoes: Dict[str, Quote] = {}
    falhas_seguidas = 0
    for lote in _lotes(simbolos):
        # Um lote que falha não invalida os demais.
        encontradas = _interpretar(_baixar(_url(lote, chave), chave, espera))
        if encontradas:
            cotacoes.update(encontradas)
            continue
        if len(lote) < 2 or falhas_seguidas >= _FALHAS_SEGUIDAS_MAX:
            continue
        for ticker in lote:
            individual = _interpretar(_baixar(_url([ticker], chave), chave, espera))
            if individual:
                cotacoes.update(individual)
                falhas_seguidas = 0
                continue
            falhas_seguidas += 1
            if falhas_seguidas >= _FALHAS_SEGUIDAS_MAX:
                break
    return cotacoes


def tickers_atualizaveis(positions: Optional[Sequence[Position]]) -> List[str]:
    """Tickers que valem uma cotação: com código e sem ``price_locked``."""
    return _tickers_unicos(
        [
            _campo_ticker(posicao)
            for posicao in (positions or ())
            if posicao is not None and not _travado(posicao)
        ]
    )


def apply_quotes(
    positions: List[Position],
    quotes: Dict[str, Quote],
) -> Tuple[List[Position], List[str]]:
    """Aplica as cotações às posições.

    Devolve ``(novas_posições, avisos)``. A lista original não é modificada
    (``Position`` é frozen: cada preço novo gera uma cópia). Posições com
    ``price_locked`` — resíduo PATL11 marcado "não atualizar", Renda Fixa
    privada e Tesouro Direto — mantêm o preço da planilha, e cada ticker sem
    cotação vira um aviso em pt-BR (sem repetir o mesmo ticker).
    """
    originais = [posicao for posicao in (positions or ()) if posicao is not None]
    mapa = _mapa_cotacoes(quotes)

    novas: List[Position] = []
    avisos: List[str] = []
    avisados: set[str] = set()

    for posicao in originais:
        ticker = _normalizar_ticker(_campo_ticker(posicao))
        if _travado(posicao) or not ticker:
            novas.append(posicao)  # preço congelado ou linha sem código
            continue
        cotacao = mapa.get(ticker)
        preco = _numero(getattr(cotacao, "price", None)) if cotacao is not None else None
        if preco is None or preco <= 0:
            novas.append(posicao)
            if ticker not in avisados:
                avisados.add(ticker)
                avisos.append(
                    f"Cotação não encontrada para {ticker} — preço da planilha mantido."
                )
            continue
        novas.append(_com_preco(posicao, preco))

    return novas, avisos


# --------------------------------------------------------------------------
# Auxiliares de posição
# --------------------------------------------------------------------------


def _campo_ticker(posicao: Any) -> str:
    return _texto(getattr(posicao, "ticker", ""))


def _travado(posicao: Any) -> bool:
    """``price_locked`` da posição; qualquer erro conta como travado.

    Na dúvida preservamos o preço da planilha — atualizar um título de Renda
    Fixa com a cotação de um ticker homônimo estragaria o patrimônio.
    """
    try:
        return bool(getattr(posicao, "price_locked", False))
    except Exception:
        return True


def _com_preco(posicao: Position, preco: float) -> Position:
    """Cópia da posição com o preço novo (mantém a original se algo falhar)."""
    if _numero(getattr(posicao, "price", None)) == preco:
        return posicao
    try:
        return replace(posicao, price=preco)
    except Exception:
        return posicao


def _mapa_cotacoes(quotes: Optional[Dict[str, Quote]]) -> Dict[str, Quote]:
    """Índice ``{TICKER maiúsculo: Quote}``, tolerante a chaves sujas."""
    mapa: Dict[str, Quote] = {}
    if not isinstance(quotes, dict):
        return mapa
    for chave, cotacao in quotes.items():
        if cotacao is None:
            continue
        ticker = _normalizar_ticker(chave) or _normalizar_ticker(
            getattr(cotacao, "ticker", "")
        )
        if ticker:
            mapa.setdefault(ticker, cotacao)
    return mapa
