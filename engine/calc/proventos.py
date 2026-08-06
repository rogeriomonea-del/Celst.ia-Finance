"""Agregação dos proventos recebidos no período (aba Proventos).

Substitui os ``SUMIF`` e o bloco "Top 10 por ativo (período)" da planilha.
Módulo **puro**: recebe ``WorkbookInputs``/``PortfolioResult`` e devolve
``ProventosResult``. Nenhuma soma é arredondada — os totais são acumulados na
ordem das linhas para reproduzir bit a bit o que o Excel calculou.

Formato dos dicionários devolvidos (chaves em inglês, snake_case):

``by_ticker`` e ``top`` (``top`` = os 10 primeiros de ``by_ticker``)
    ``ticker``       chave de agrupamento (o código em maiúsculas)
    ``label``        rótulo para exibição (ver ``_rotulo``: para papéis
                     genéricos como CRA/CRI vira ``"CRA - CRA020001US"``)
    ``product``      primeira descrição completa encontrada para o ticker
    ``amount``       soma recebida no período (R$)
    ``share``        ``amount / total`` (0.0 se ``total`` for 0)
    ``count``        quantidade de lançamentos
    ``last_paid_at`` data do último pagamento (``None`` se nenhum tiver data)

``by_kind``
    ``kind``       tipo canônico (Rendimento, Dividendo, JCP, Juros,
                   Amortização, Bonificação, Fração, Aluguel, Outros)
    ``raw_kinds``  rótulos originais da planilha que caíram nesse tipo
    ``amount`` / ``share`` / ``count``  como acima

``by_month`` (ordem cronológica)
    ``month``   chave ``"AAAA-MM"``
    ``label``   rótulo pt-BR (``"jun/2026"``)
    ``amount`` / ``share`` / ``count``  como acima

Lançamentos sem data entram em ``total``, ``by_ticker`` e ``by_kind``, mas
ficam fora de ``by_month`` e do período — por isso a soma de ``share`` em
``by_month`` pode ser menor que 1.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..models import PortfolioResult, Provento, ProventosResult, WorkbookInputs

#: Quantos ativos entram em ``ProventosResult.top``.
TOP_N = 10

_MESES_PT = (
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)

_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")

# Ticker "de verdade" da B3: 4 letras + 1 ou 2 dígitos (+ letra opcional).
_TICKER_B3 = re.compile(r"^[A-Z]{4}\d{1,2}[A-Z]?$")

# Ordem importa: "juros sobre capital" tem de ser testado antes de "juros".
_TIPOS_CANONICOS: Tuple[Tuple[str, str], ...] = (
    ("juros sobre capital", "JCP"),
    ("jcp", "JCP"),
    ("amortiza", "Amortização"),
    ("rendimento", "Rendimento"),
    ("dividendo", "Dividendo"),
    ("juros", "Juros"),
    ("bonifica", "Bonificação"),
    ("fracao", "Fração"),
    ("aluguel", "Aluguel"),
)


# --------------------------------------------------------------------------
# Auxiliares
# --------------------------------------------------------------------------


def _normaliza(texto: str) -> str:
    """Minúsculas, sem acento e sem pontuação — só para comparar rótulos."""
    if not texto:
        return ""
    limpo = str(texto).strip().lower().translate(_ACENTOS)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", limpo)).strip()


def _tipo_canonico(bruto: str) -> str:
    """'AMORTIZACAO PROGRAMADA' -> 'Amortização'; 'PAGAMENTO DE JUROS' -> 'Juros'."""
    texto = _normaliza(bruto)
    if not texto:
        return "Outros"
    for chave, rotulo in _TIPOS_CANONICOS:
        if chave in texto:
            return rotulo
    return str(bruto).strip() or "Outros"


def _chave_ticker(lancamento: Provento) -> str:
    """Chave de agrupamento: o ticker; sem ele, o 1º trecho do produto."""
    ticker = (lancamento.ticker or "").strip().upper()
    if ticker:
        return ticker
    produto = (lancamento.product or "").strip()
    if produto:
        return produto.split(" - ")[0].strip().upper() or "(sem ticker)"
    return "(sem ticker)"


def _codigo_do_produto(ticker: str, produto: str) -> str:
    """Código específico em 'CRA - CRA020001US - ECO SECURITIZADORA...' -> 'CRA020001US'."""
    partes = [parte.strip() for parte in str(produto or "").split(" - ") if parte.strip()]
    if len(partes) < 2 or partes[0].upper() != ticker:
        return ""
    return partes[1]


def _rotulo(ticker: str, produtos: Sequence[str]) -> str:
    """Rótulo de exibição do grupo.

    Tickers normais da B3 se identificam sozinhos. Papéis genéricos (CRA, CRI,
    DEB…) compartilham o mesmo "ticker" na B3, então o rótulo inclui o código
    do papel quando todos os lançamentos do grupo apontam para o mesmo.
    """
    if _TICKER_B3.match(ticker):
        return ticker
    codigos = {c for c in (_codigo_do_produto(ticker, p) for p in produtos) if c}
    if len(codigos) == 1:
        return f"{ticker} - {codigos.pop()}"
    return ticker


def _chave_mes(dia: date) -> str:
    return f"{dia.year:04d}-{dia.month:02d}"


def _rotulo_mes(chave: str) -> str:
    """'2026-06' -> 'jun/2026'."""
    ano, _, mes = chave.partition("-")
    try:
        indice = int(mes)
    except ValueError:
        return chave
    if not 1 <= indice <= 12:
        return chave
    return f"{_MESES_PT[indice - 1]}/{ano}"


def _fracao(parte: float, total: float) -> float:
    """Divisão que nunca levanta exceção."""
    if not total:
        return 0.0
    return parte / total


# --------------------------------------------------------------------------
# Cálculo
# --------------------------------------------------------------------------


def compute_proventos(
    inputs: WorkbookInputs,
    portfolio: Optional[PortfolioResult] = None,
) -> ProventosResult:
    """Consolida os proventos do período.

    ``portfolio`` é opcional e serve só para o ``yield_on_portfolio``
    (total recebido ÷ valor de mercado da carteira).
    """
    lancamentos: List[Provento] = list(getattr(inputs, "proventos", None) or [])

    total = 0.0
    for lancamento in lancamentos:
        total += float(lancamento.amount or 0.0)

    # ---- por ticker (mesma semântica do SUMIF por Ticker/Código) ----------
    soma_ticker: Dict[str, float] = {}
    contagem_ticker: Dict[str, int] = {}
    produtos_ticker: Dict[str, List[str]] = {}
    ultima_data: Dict[str, Optional[date]] = {}

    # ---- por tipo (rótulos da B3 normalizados) ---------------------------
    soma_tipo: Dict[str, float] = {}
    contagem_tipo: Dict[str, int] = {}
    brutos_tipo: Dict[str, List[str]] = {}

    # ---- por mês ---------------------------------------------------------
    soma_mes: Dict[str, float] = {}
    contagem_mes: Dict[str, int] = {}

    datas: List[date] = []

    for lancamento in lancamentos:
        valor = float(lancamento.amount or 0.0)

        ticker = _chave_ticker(lancamento)
        soma_ticker[ticker] = soma_ticker.get(ticker, 0.0) + valor
        contagem_ticker[ticker] = contagem_ticker.get(ticker, 0) + 1
        ultima_data.setdefault(ticker, None)
        produtos = produtos_ticker.setdefault(ticker, [])
        produto = (lancamento.product or "").strip()
        if produto:
            produtos.append(produto)

        tipo = _tipo_canonico(lancamento.kind or "")
        soma_tipo[tipo] = soma_tipo.get(tipo, 0.0) + valor
        contagem_tipo[tipo] = contagem_tipo.get(tipo, 0) + 1
        brutos = brutos_tipo.setdefault(tipo, [])
        bruto = (lancamento.kind or "").strip()
        if bruto and bruto not in brutos:
            brutos.append(bruto)

        dia = lancamento.paid_at
        if isinstance(dia, date):
            datas.append(dia)
            anterior = ultima_data.get(ticker)
            if anterior is None or dia > anterior:
                ultima_data[ticker] = dia
            mes = _chave_mes(dia)
            soma_mes[mes] = soma_mes.get(mes, 0.0) + valor
            contagem_mes[mes] = contagem_mes.get(mes, 0) + 1

    by_ticker: List[Dict[str, Any]] = [
        {
            "ticker": ticker,
            "label": _rotulo(ticker, produtos_ticker.get(ticker, [])),
            "product": next(iter(produtos_ticker.get(ticker, [])), ""),
            "amount": valor,
            "share": _fracao(valor, total),
            "count": contagem_ticker.get(ticker, 0),
            "last_paid_at": ultima_data.get(ticker),
        }
        for ticker, valor in soma_ticker.items()
    ]
    by_ticker.sort(key=lambda item: (-item["amount"], item["ticker"]))

    by_kind: List[Dict[str, Any]] = [
        {
            "kind": tipo,
            "raw_kinds": list(brutos_tipo.get(tipo, [])),
            "amount": valor,
            "share": _fracao(valor, total),
            "count": contagem_tipo.get(tipo, 0),
        }
        for tipo, valor in soma_tipo.items()
    ]
    by_kind.sort(key=lambda item: (-item["amount"], item["kind"]))

    by_month: List[Dict[str, Any]] = [
        {
            "month": mes,
            "label": _rotulo_mes(mes),
            "amount": soma_mes[mes],
            "share": _fracao(soma_mes[mes], total),
            "count": contagem_mes.get(mes, 0),
        }
        for mes in sorted(soma_mes)
    ]

    valor_carteira = float(getattr(portfolio, "total_value", 0.0) or 0.0) if portfolio else 0.0

    return ProventosResult(
        total=total,
        by_ticker=by_ticker,
        by_kind=by_kind,
        by_month=by_month,
        top=[dict(item) for item in by_ticker[:TOP_N]],
        period_start=min(datas) if datas else None,
        period_end=max(datas) if datas else None,
        monthly_average=_fracao(total, float(len(soma_mes))),
        yield_on_portfolio=_fracao(total, valor_carteira),
    )
