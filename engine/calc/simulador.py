"""Simulador de juros compostos (aba ``Simulador``).

Reproduz, em Python puro, as três coisas que a planilha calculava:

* a **taxa mensal efetiva** equivalente à taxa anual — ``(1+a)^(1/12) - 1``;
* o **valor final** pela fórmula fechada da anuidade
  ``PV*(1+i)^n + PMT*(((1+i)^n - 1)/i)``, arredondado a 2 casas como o
  ``ROUND`` da célula B16;
* a **evolução mês a mês** pela recursão ``saldo = saldo*(1+i) + PMT``,
  começando no mês 0 com o capital inicial.

O aporte mensal é ``aporte - retirada``: se a retirada for maior, o ``PMT``
fica negativo e a simulação vira um plano de resgates (é o que a dica da
célula A18 da planilha sugere ao usuário).

Módulo **puro**: entram dataclasses de ``engine.models``, saem dataclasses de
``engine.models``. Sem I/O, sem rede, sem openpyxl.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional

from ..models import Assumptions, SimulationPoint, SimulationResult

# Horizonte usado quando nem ``months`` nem as datas do Config dizem outra
# coisa: 4 anos, o mesmo período que a planilha traz preenchido (B7 = 4).
MESES_PADRAO: int = 48

# A planilha desenha a evolução mensal "até 100 anos"; usamos o mesmo teto para
# que um intervalo absurdo (datas trocadas, ano digitado errado) não gere uma
# série de milhões de pontos.
MESES_MAXIMO: int = 1200

# Ano médio do calendário gregoriano — inclui os bissextos na conversão de
# dias para anos.
DIAS_POR_ANO: float = 365.25


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _numero(valor: Any) -> float:
    """Converte para ``float`` tratando ``None``, texto inválido, NaN e infinito."""
    # Booleano é lixo de planilha numa coluna de dinheiro: ``True`` virar 1,00
    # inventaria patrimônio, então tratamos como ausência de valor.
    if valor is None or isinstance(valor, bool):
        return 0.0
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return 0.0
    # NaN não é igual a si mesmo; infinito envenenaria todas as somas seguintes.
    if numero != numero or numero in (float("inf"), float("-inf")):
        return 0.0
    return numero


def _potencia(base: float, expoente: float) -> float:
    """``base ** expoente`` que devolve 0.0 em vez de estourar ou virar complexo."""
    # Base negativa com expoente fracionário devolve ``complex`` em Python 3;
    # expoente grande levanta ``OverflowError``. Nos dois casos não há número
    # útil a devolver.
    if base < 0:
        return 0.0
    try:
        resultado = base**expoente
    except (OverflowError, ValueError, ZeroDivisionError):
        return 0.0
    return _numero(resultado)


def _divide(numerador: float, denominador: float) -> float:
    """Divisão que devolve 0.0 em vez de estourar quando o denominador é zero."""
    if not denominador:
        return 0.0
    try:
        resultado = numerador / denominador
    except (ZeroDivisionError, OverflowError):
        return 0.0
    return _numero(resultado)


def _arredonda(valor: float) -> float:
    """Duas casas decimais, como o ``ROUND(...;2)`` das células de resultado."""
    numero = _numero(valor)
    try:
        return round(numero, 2)
    except (OverflowError, ValueError):  # pragma: no cover - já filtrado por _numero
        return 0.0


def _como_data(valor: Any) -> Optional[date]:
    """Aceita ``date`` e ``datetime``; qualquer outra coisa vira ``None``."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


# --------------------------------------------------------------------------
# Taxa e horizonte
# --------------------------------------------------------------------------


def taxa_mensal(taxa_anual: float) -> float:
    """Taxa mensal efetiva equivalente à anual: ``(1+a)^(1/12) - 1``.

    Uma taxa anual de -100% ou pior (o capital some) é tratada como -100% ao
    mês, em vez de virar raiz de número negativo.
    """
    anual = _numero(taxa_anual)
    if anual == 0.0:
        return 0.0
    base = 1.0 + anual
    if base <= 0.0:
        return -1.0
    return _potencia(base, 1.0 / 12.0) - 1.0


def _meses(assumptions: Optional[Assumptions], months: Optional[int]) -> int:
    """Quantos meses simular.

    Ordem de precedência: o argumento ``months``; senão o intervalo
    ``start_date`` → ``end_date`` do Config convertido em **anos inteiros**
    × 12 (é o que a planilha faz: B7 = 4 ano(s) → B12 = 48 meses); senão
    :data:`MESES_PADRAO`.
    """
    if months is not None:
        try:
            explicito = int(months)
        except (TypeError, ValueError):
            explicito = MESES_PADRAO
        return max(0, min(explicito, MESES_MAXIMO))

    inicio = _como_data(getattr(assumptions, "start_date", None))
    fim = _como_data(getattr(assumptions, "end_date", None))
    if inicio is not None and fim is not None:
        dias = (fim - inicio).days
        if dias > 0:
            anos = int(round(dias / DIAS_POR_ANO))
            if anos > 0:
                return min(anos * 12, MESES_MAXIMO)
    return MESES_PADRAO


# --------------------------------------------------------------------------
# Fórmula fechada e série mensal
# --------------------------------------------------------------------------


def _valor_final(inicial: float, aporte: float, taxa: float, meses: int) -> float:
    """``PV*(1+i)^n + PMT*(((1+i)^n - 1)/i)``, com o ramo ``i == 0``.

    Sem juros a anuidade degenera em soma simples (``PV + PMT*n``) — e é
    obrigatório tratar esse ramo à parte, porque a fórmula geral divide por
    ``i``.
    """
    if taxa == 0.0:
        return inicial + aporte * meses
    fator = _potencia(1.0 + taxa, meses)
    return inicial * fator + aporte * _divide(fator - 1.0, taxa)


def _serie(inicial: float, aporte: float, taxa: float, meses: int) -> List[SimulationPoint]:
    """Evolução mês a mês pela recursão ``saldo = saldo*(1+i) + PMT``.

    O mês 0 é a foto de hoje: saldo = investido = capital inicial, juros = 0.
    """
    pontos: List[SimulationPoint] = []
    saldo = inicial
    for mes in range(meses + 1):
        if mes:
            saldo = _numero(saldo * (1.0 + taxa) + aporte)
        investido = inicial + aporte * mes
        pontos.append(
            SimulationPoint(
                month=mes,
                balance=_numero(saldo),
                invested=_numero(investido),
                interest=_numero(saldo - investido),
            )
        )
    return pontos


# --------------------------------------------------------------------------
# Entrada pública
# --------------------------------------------------------------------------


def compute_simulation(
    assumptions: Assumptions,
    annual_rate: float,
    months: Optional[int] = None,
) -> SimulationResult:
    """Simula juros compostos com aporte mensal constante.

    ``annual_rate`` costuma ser o retorno esperado dos cenários; o capital
    inicial e o aporte vêm do Config. Devolve sempre um
    :class:`SimulationResult` válido: parâmetros nulos, taxa zero ou horizonte
    zero produzem zeros, nunca exceção.
    """
    inicial = _numero(getattr(assumptions, "initial_capital", 0.0))
    # PMT = aporte - retirada. Negativo é permitido: vira plano de resgates.
    aporte = _numero(getattr(assumptions, "monthly_contribution", 0.0)) - _numero(
        getattr(assumptions, "monthly_withdrawal", 0.0)
    )
    anual = _numero(annual_rate)
    mensal = taxa_mensal(anual)
    meses = _meses(assumptions, months)

    # O painel de resultado da planilha usa a fórmula fechada (e não o último
    # ponto da série): mantemos a mesma fonte para bater centavo a centavo.
    final = _arredonda(_valor_final(inicial, aporte, mensal, meses))
    investido = _arredonda(inicial + aporte * meses)
    juros = _arredonda(final - investido)

    return SimulationResult(
        initial=inicial,
        monthly=aporte,
        annual_rate=anual,
        monthly_rate=mensal,
        months=meses,
        final_balance=final,
        total_invested=investido,
        total_interest=juros,
        series=_serie(inicial, aporte, mensal, meses),
    )
