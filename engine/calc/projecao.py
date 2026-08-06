"""Projeção do patrimônio e da renda até 2030 (aba ``Projecao_2030``).

A planilha projeta **dois regimes** em paralelo, a partir dos mesmos
parâmetros do Config:

* **Renda** — carteira montada para distribuir caixa (mais pós-fixado, mais
  FIIs, títulos IPCA+ carregados até o vencimento);
* **Valorização** — carteira montada para crescer (mais ações, IPCA+ longo
  marcado a mercado, prêmios de reprecificação nos ativos de risco).

Cada regime tem um retorno anual próprio, calculado como a soma ponderada das
fatias da carteira (:func:`annual_returns`). A partir daí a evolução mensal é
``valor_t = valor_{t-1} * (1+r)^(1/12) + aporte``, e a renda mensal parte da
meta do Config e cresce pela inflação.

Módulo **puro**: entram dataclasses de ``engine.models``, saem dataclasses de
``engine.models``. Sem I/O, sem rede, sem openpyxl.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, List, Optional, Tuple

from ..models import Assumptions, ProjectionPoint, ProjectionResult

# Horizonte usado quando nem ``months`` nem as datas do Config dizem outra
# coisa: os mesmos 58 meses que a planilha traz preenchidos.
MESES_PADRAO: int = 58

# Teto defensivo: datas trocadas ou um ano digitado errado não podem gerar uma
# série de milhões de pontos.
MESES_MAXIMO: int = 1200

# Rótulo do cenário que seleciona o regime de renda (Config B3).
CENARIO_RENDA: str = "renda"

_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")
_NAO_ALFANUM = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _numero(valor: Any) -> float:
    """Converte para ``float`` tratando ``None``, texto inválido, NaN e infinito."""
    if valor is None or isinstance(valor, bool):
        return 0.0
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return 0.0
    if numero != numero or numero in (float("inf"), float("-inf")):
        return 0.0
    return numero


def _potencia(base: float, expoente: float) -> float:
    """``base ** expoente`` que devolve 0.0 em vez de estourar ou virar complexo."""
    if base < 0:
        return 0.0
    try:
        resultado = base**expoente
    except (OverflowError, ValueError, ZeroDivisionError):
        return 0.0
    return _numero(resultado)


def _taxa_mensal(taxa_anual: float) -> float:
    """Taxa mensal efetiva equivalente à anual: ``(1+a)^(1/12) - 1``."""
    anual = _numero(taxa_anual)
    if anual == 0.0:
        return 0.0
    base = 1.0 + anual
    if base <= 0.0:
        return -1.0
    return _potencia(base, 1.0 / 12.0) - 1.0


def _como_data(valor: Any) -> Optional[date]:
    """Aceita ``date`` e ``datetime``; qualquer outra coisa vira ``None``."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


def _chave(texto: Any) -> str:
    """Minúsculas, sem acento e sem pontuação — para comparar o nome do cenário."""
    if texto is None:
        return ""
    limpo = str(texto).strip().lower().translate(_ACENTOS)
    return _NAO_ALFANUM.sub(" ", limpo).strip()


def _primeiro_dia(momento: date) -> date:
    return date(momento.year, momento.month, 1)


def _soma_meses(base: date, delta: int) -> date:
    """Primeiro dia do mês ``delta`` meses depois de ``base`` (delta pode ser negativo)."""
    total = base.year * 12 + (base.month - 1) + delta
    ano, mes = divmod(total, 12)
    try:
        return date(ano, mes + 1, 1)
    except ValueError:
        # Estourou o calendário (ano < 1 ou > 9999): trava no limite.
        return date.min if ano < 1 else date(9999, 12, 1)


# --------------------------------------------------------------------------
# Retorno anual de cada regime
# --------------------------------------------------------------------------


def annual_returns(assumptions: Assumptions) -> Tuple[float, float]:
    """``(renda, valorizacao)`` — o retorno anual de cada regime.

    É a soma ponderada das fatias da carteira. Os pesos são os da planilha
    (somam 1,00 em cada regime) e estão escritos literalmente para que a
    conferência contra o arquivo original seja linha a linha.
    """
    selic = _numero(getattr(assumptions, "selic_annual", 0.0))
    ipca = _numero(getattr(assumptions, "inflation_annual", 0.0))
    real = _numero(getattr(assumptions, "real_rate_annual", 0.0))
    delta_real = _numero(getattr(assumptions, "real_rate_delta", 0.0))
    duration = _numero(getattr(assumptions, "duration_years", 0.0))
    dy_fii = _numero(getattr(assumptions, "dy_fii", 0.0))
    preco_fii = _numero(getattr(assumptions, "price_return_fii", 0.0))
    dy_acoes = _numero(getattr(assumptions, "dy_stocks", 0.0))
    preco_acoes = _numero(getattr(assumptions, "price_return_stocks", 0.0))
    exterior = _numero(getattr(assumptions, "foreign_return", 0.0))

    ipca_mais = ipca + real  # título IPCA+ levado até o vencimento
    acoes = dy_acoes + preco_acoes  # retorno total de ações: dividendo + preço
    fiis = dy_fii + preco_fii  # retorno total de FIIs: rendimento + cota

    # --- Regime Renda (pesos: 17,5 + 27 + 25 + 20,5 + 5 + 5 = 100%) --------
    renda = (
        # 17,5% pós-fixado (Selic/CDI) — o colchão de liquidez.
        0.175 * selic
        # 27% em IPCA+ repartido em três vencimentos (10% + 7% + 10%), todos
        # carregados até o vencimento: rendem ipca + juro real, sem marcação.
        + 0.10 * ipca_mais
        + 0.07 * ipca_mais
        + 0.10 * ipca_mais
        # 25% FIIs: rendimento distribuído + valorização da cota.
        + 0.25 * fiis
        # 25,5% ações repartido em blocos (20,5% grandes + 5% small caps),
        # aqui sem prêmio nenhum — a carteira de renda não conta com
        # reprecificação.
        + 0.205 * acoes
        + 0.05 * acoes
        # 5% exterior.
        + 0.05 * exterior
    )

    # --- Regime Valorização (9,5 + 20 + 20 + 30,5 + 10 + 10 = 100%) --------
    # Ganho de marcação a mercado do IPCA+ longo: uma queda de ``delta_real``
    # na taxa real valoriza o título em ``-duration × delta``.
    marcacao = -duration * delta_real
    valorizacao = (
        # 9,5% pós-fixado — caixa menor que no regime de renda.
        0.095 * selic
        # 5% IPCA+ curto, sem marcação.
        + 0.05 * ipca_mais
        # 15% IPCA+ longo, este sim marcado a mercado.
        + 0.15 * (ipca_mais + marcacao)
        # 20% FIIs com 3 p.p. de reprecificação das cotas.
        + 0.20 * (fiis + 0.03)
        # 40,5% ações: 30,5% grandes com +2 p.p. e 10% small caps com +3 p.p.
        + 0.305 * (acoes + 0.02)
        + 0.10 * (acoes + 0.03)
        # 10% exterior.
        + 0.10 * exterior
    )

    return _numero(renda), _numero(valorizacao)


# --------------------------------------------------------------------------
# Horizonte da projeção
# --------------------------------------------------------------------------


def _meses(assumptions: Optional[Assumptions], months: Optional[int]) -> int:
    """Quantos meses projetar: ``months``, senão start→end (inclusivo), senão 58."""
    if months is not None:
        try:
            explicito = int(months)
        except (TypeError, ValueError):
            explicito = MESES_PADRAO
        return max(0, min(explicito, MESES_MAXIMO))

    inicio = _como_data(getattr(assumptions, "start_date", None))
    fim = _como_data(getattr(assumptions, "end_date", None))
    if inicio is not None and fim is not None:
        # Contagem inclusiva de meses-calendário: mar/2026 → dez/2030 = 58.
        total = (fim.year - inicio.year) * 12 + (fim.month - inicio.month) + 1
        if total > 0:
            return min(total, MESES_MAXIMO)
    return MESES_PADRAO


def _mes_inicial(assumptions: Optional[Assumptions], meses: int) -> date:
    """Primeiro mês da série: a data inicial do Config, ou o que der para inferir.

    Sem ``start_date``, mas com ``end_date``, andamos para trás a partir do
    fim. Sem nenhuma das duas, a projeção começa no mês corrente — é o que o
    usuário espera de uma projeção "a partir de agora".
    """
    inicio = _como_data(getattr(assumptions, "start_date", None))
    if inicio is not None:
        return _primeiro_dia(inicio)
    fim = _como_data(getattr(assumptions, "end_date", None))
    if fim is not None and meses > 0:
        return _soma_meses(_primeiro_dia(fim), -(meses - 1))
    return _primeiro_dia(date.today())


# --------------------------------------------------------------------------
# Entrada pública
# --------------------------------------------------------------------------


def compute_projection(
    assumptions: Assumptions,
    months: Optional[int] = None,
) -> ProjectionResult:
    """Projeta patrimônio e renda mês a mês nos dois regimes.

    A primeira linha já é o **primeiro mês rendendo**: o capital inicial
    cresce um mês e recebe o primeiro aporte (é assim na planilha). A renda
    mensal, ao contrário, começa exatamente na meta do Config e só passa a
    corrigir pela inflação a partir do segundo mês.

    Devolve sempre um :class:`ProjectionResult` válido: parâmetros nulos ou
    horizonte zero produzem zeros e série vazia, nunca exceção.
    """
    renda_aa, valorizacao_aa = annual_returns(assumptions)
    taxa_renda = _taxa_mensal(renda_aa)
    taxa_valorizacao = _taxa_mensal(valorizacao_aa)
    inflacao_mensal = _taxa_mensal(getattr(assumptions, "inflation_annual", 0.0))

    capital = _numero(getattr(assumptions, "initial_capital", 0.0))
    # Aporte líquido do mês = aporte - retirada (Config B5 - B23).
    aporte = _numero(getattr(assumptions, "monthly_contribution", 0.0)) - _numero(
        getattr(assumptions, "monthly_withdrawal", 0.0)
    )
    cenario = str(getattr(assumptions, "scenario", "") or "")
    prefere_renda = _chave(cenario) == CENARIO_RENDA

    meses = _meses(assumptions, months)
    primeiro_mes = _mes_inicial(assumptions, meses)

    valor_renda = capital
    valor_valorizacao = capital
    renda_mensal = _numero(getattr(assumptions, "monthly_income_target", 0.0))

    serie: List[ProjectionPoint] = []
    for indice in range(meses):
        valor_renda = _numero(valor_renda * (1.0 + taxa_renda) + aporte)
        valor_valorizacao = _numero(valor_valorizacao * (1.0 + taxa_valorizacao) + aporte)
        if indice:
            # A renda alvo do 1º mês é a do Config; daí em diante ela só
            # acompanha a inflação, para preservar poder de compra.
            renda_mensal = _numero(renda_mensal * (1.0 + inflacao_mensal))
        serie.append(
            ProjectionPoint(
                month=_soma_meses(primeiro_mes, indice),
                contribution=aporte,
                value_income=valor_renda,
                income_income=renda_mensal,
                value_growth=valor_valorizacao,
                income_growth=renda_mensal,
                value_selected=valor_renda if prefere_renda else valor_valorizacao,
                income_selected=renda_mensal,
            )
        )

    ultimo = serie[-1] if serie else None
    return ProjectionResult(
        annual_return_income=renda_aa,
        annual_return_growth=valorizacao_aa,
        scenario=cenario,
        series=serie,
        final_value=ultimo.value_selected if ultimo else 0.0,
        final_income=ultimo.income_selected if ultimo else 0.0,
    )
