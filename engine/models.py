"""Tipos de domínio do motor de planilhas.

Todos os dataclasses aqui são serializáveis em JSON via ``asdict`` e não
dependem de openpyxl: os módulos de cálculo trabalham só com estes tipos.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# Entradas extraídas da planilha
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    """Uma linha da aba Posicao_B3."""

    asset_class: str
    ticker: str
    name: str
    quantity: float
    price: float
    var_12m: Optional[float] = None
    note: str = ""
    price_earnings: Optional[float] = None
    price_to_book: Optional[float] = None
    dividend_yield: Optional[float] = None
    technical_signal: str = ""
    fundamental_read: str = ""
    row: int = 0

    @property
    def market_value(self) -> float:
        return self.quantity * self.price

    @property
    def price_locked(self) -> bool:
        """Preço não deve ser atualizado por cotação (resíduo, RF, Tesouro)."""
        if "não atualizar" in self.note.lower():
            return True
        return self.asset_class in ("Renda Fixa privada", "Tesouro Direto")


@dataclass(frozen=True)
class Provento:
    """Uma linha da aba Proventos."""

    paid_at: Optional[date]
    kind: str
    ticker: str
    product: str
    amount: float


@dataclass(frozen=True)
class CashflowEntry:
    """Uma linha diária da aba 'Fluxo de Caixa'."""

    day: date
    inflow: float = 0.0
    invest_contribution: float = 0.0
    reserve_contribution: float = 0.0
    card_bill: float = 0.0
    expenses: float = 0.0

    @property
    def total_expenses(self) -> float:
        return self.card_bill + self.expenses

    @property
    def savings(self) -> float:
        return self.inflow - self.total_expenses


@dataclass(frozen=True)
class ConsolidatedAccount:
    """Uma linha da aba 'Posicao Consolidada'."""

    institution: str
    category: str
    liquidity: str
    value: float


@dataclass(frozen=True)
class ForeignPosition:
    """Uma linha da aba Dolarizado (valores em US$)."""

    broker: str
    ticker: str
    description: str
    quantity: float
    price_usd: float
    cost_usd: float = 0.0

    @property
    def value_usd(self) -> float:
        return self.quantity * self.price_usd

    @property
    def result_usd(self) -> float:
        return self.value_usd - self.cost_usd

    @property
    def result_percent(self) -> float:
        return 0.0 if self.cost_usd == 0 else self.result_usd / self.cost_usd


@dataclass(frozen=True)
class ScenarioClass:
    """Uma classe na matriz de cenários (Cenarios!B20:G26)."""

    name: str
    weight: float
    returns: Dict[str, float]


@dataclass(frozen=True)
class Assumptions:
    """Parâmetros da aba Config."""

    scenario: str = "Valorizacao"
    initial_capital: float = 0.0
    monthly_contribution: float = 0.0
    monthly_withdrawal: float = 0.0
    inflation_annual: float = 0.0414
    selic_annual: float = 0.1475
    real_rate_annual: float = 0.05
    real_rate_delta: float = -0.01
    duration_years: float = 24.0
    dy_fii: float = 0.095
    dy_stocks: float = 0.07
    price_return_stocks: float = 0.06
    price_return_fii: float = 0.03
    foreign_return: float = 0.04
    order_tolerance: float = 500.0
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    target_monthly_real: float = 0.01
    tax_and_costs: float = 0.10
    monthly_income_target: float = 0.0
    small_cap_premium: float = 0.02
    dy_small_caps: float = 0.02
    dy_foreign: float = 0.015

    @property
    def inflation_monthly(self) -> float:
        return (1 + self.inflation_annual) ** (1 / 12) - 1


@dataclass(frozen=True)
class RebalanceTarget:
    """Alvo por classe da aba Rebalanceamento (coluna E)."""

    asset_class: str
    target: float


@dataclass(frozen=True)
class SimulatorInputs:
    """Entradas próprias da aba Simulador.

    Ela é uma calculadora independente: o valor inicial e o aporte são digitados
    ali e não referenciam o Config, então podem divergir dele com o tempo.
    """

    initial: float
    monthly: float
    annual_rate: Optional[float] = None
    months: Optional[int] = None


@dataclass
class WorkbookInputs:
    """Tudo que foi extraído da pasta de trabalho, já tipado."""

    positions: List[Position] = field(default_factory=list)
    proventos: List[Provento] = field(default_factory=list)
    cashflow: List[CashflowEntry] = field(default_factory=list)
    accounts: List[ConsolidatedAccount] = field(default_factory=list)
    foreign: List[ForeignPosition] = field(default_factory=list)
    scenario_classes: List[ScenarioClass] = field(default_factory=list)
    scenario_probabilities: Dict[str, float] = field(default_factory=dict)
    inflation_forecast: List[Dict[str, float]] = field(default_factory=list)
    rebalance_targets: List[RebalanceTarget] = field(default_factory=list)
    assumptions: Assumptions = field(default_factory=Assumptions)
    simulator: Optional[SimulatorInputs] = None
    usd_brl: float = 0.0
    opportunity_reserve: float = 0.0
    period_label: str = ""
    source_kind: str = "carteira_b3"
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Saídas calculadas
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionResult:
    ticker: str
    name: str
    asset_class: str
    quantity: float
    price: float
    market_value: float
    portfolio_weight: float
    proventos_period: float
    var_12m: Optional[float]
    price_earnings: Optional[float]
    price_to_book: Optional[float]
    dividend_yield: Optional[float]
    technical_signal: str
    fundamental_read: str
    note: str


@dataclass(frozen=True)
class ClassSummary:
    asset_class: str
    value: float
    weight: float
    positions: int


@dataclass(frozen=True)
class PortfolioResult:
    total_value: float
    total_proventos: float
    positions_count: int
    weighted_var_12m: float
    positions: List[PositionResult]
    by_class: List[ClassSummary]


@dataclass(frozen=True)
class RebalanceRow:
    asset_class: str
    current_value: float
    current_weight: float
    target_weight: float
    band_min: float
    band_max: float
    action: str
    deviation: float
    amount: float


@dataclass(frozen=True)
class RebalanceResult:
    rows: List[RebalanceRow]
    total_value: float
    targets_sum: float
    band_relative: float
    contribution_plan: List[Dict[str, Any]]
    notes: List[str]


@dataclass(frozen=True)
class SimulationPoint:
    month: int
    balance: float
    invested: float
    interest: float


@dataclass(frozen=True)
class SimulationResult:
    initial: float
    monthly: float
    annual_rate: float
    monthly_rate: float
    months: int
    final_balance: float
    total_invested: float
    total_interest: float
    series: List[SimulationPoint]


@dataclass(frozen=True)
class ProjectionPoint:
    month: date
    contribution: float
    value_income: float
    income_income: float
    value_growth: float
    income_growth: float
    value_selected: float
    income_selected: float


@dataclass(frozen=True)
class ProjectionResult:
    annual_return_income: float
    annual_return_growth: float
    scenario: str
    series: List[ProjectionPoint]
    final_value: float
    final_income: float


@dataclass(frozen=True)
class ScenarioResult:
    classes: List[ScenarioClass]
    returns_by_scenario: Dict[str, float]
    probabilities: Dict[str, float]
    expected_return: float
    inflation_average: float
    selic_average: float
    weighted_dy_stocks: float
    weighted_dy_fii: float
    weighted_pe_stocks: float
    weighted_pb_fii: float
    inflation_forecast: List[Dict[str, float]]


@dataclass(frozen=True)
class MonthlyCashflow:
    month: date
    inflow: float
    expenses: float
    invest_contribution: float
    savings: float
    invested_balance: float
    cash_balance: float
    net_worth: float


@dataclass(frozen=True)
class CashflowResult:
    monthly: List[MonthlyCashflow]
    daily_balance: List[Dict[str, Any]]
    total_inflow: float
    total_expenses: float
    total_contributions: float
    savings_rate: float
    final_invested: float


@dataclass(frozen=True)
class ProventosResult:
    total: float
    by_ticker: List[Dict[str, Any]]
    by_kind: List[Dict[str, Any]]
    by_month: List[Dict[str, Any]]
    top: List[Dict[str, Any]]
    period_start: Optional[date]
    period_end: Optional[date]
    monthly_average: float
    yield_on_portfolio: float


@dataclass(frozen=True)
class ConsolidatedResult:
    total: float
    accounts: List[Dict[str, Any]]
    by_category: List[Dict[str, Any]]
    by_liquidity: List[Dict[str, Any]]
    usd_brl: float
    foreign_total_usd: float
    foreign_total_brl: float
    foreign: List[Dict[str, Any]]
    foreign_by_broker: List[Dict[str, Any]]
    foreign_result_usd: float


@dataclass(frozen=True)
class ChartSeries:
    """Uma série pronta para o Recharts."""

    id: str
    title: str
    kind: str  # donut | bar | line | area
    x_key: str
    series: List[Dict[str, str]]
    data: List[Dict[str, Any]]
    value_format: str = "currency"  # currency | percent | number
    note: str = ""


@dataclass(frozen=True)
class Analysis:
    """Resultado completo da análise de uma pasta de trabalho."""

    file_name: str
    generated_at: str
    source_kind: str
    period_label: str
    portfolio: Optional[PortfolioResult]
    rebalance: Optional[RebalanceResult]
    simulation: Optional[SimulationResult]
    projection: Optional[ProjectionResult]
    scenarios: Optional[ScenarioResult]
    cashflow: Optional[CashflowResult]
    proventos: Optional[ProventosResult]
    consolidated: Optional[ConsolidatedResult]
    charts: List[ChartSeries]
    assumptions: Assumptions
    kpis: List[Dict[str, Any]]
    warnings: List[str]
    engine: Dict[str, Any]


# --------------------------------------------------------------------------
# Serialização
# --------------------------------------------------------------------------


def _encode(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    if isinstance(value, float):
        # NaN/Inf não são JSON válido.
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    return value


def to_dict(obj: Any) -> Any:
    """Converte dataclasses (aninhados) em estruturas JSON puras."""
    if hasattr(obj, "__dataclass_fields__"):
        return _encode(asdict(obj))
    return _encode(obj)
