/**
 * Espelho TypeScript de `engine/models.py`.
 *
 * O motor Python serializa os dataclasses com `to_dict`, então todas as chaves
 * chegam em **snake_case** e as datas viram strings ISO (`"2025-01-31"`).
 * Campos `Optional[...]` do Python viram `| null` aqui.
 *
 * Nada de `any`: os dicionários genéricos (`Dict[str, Any]`) do motor estão
 * descritos abaixo com os campos que cada módulo de cálculo realmente emite.
 */

/** Formatação que o motor sugere para os valores de um gráfico/KPI. */
export type FormatoValor = "currency" | "percent" | "number" | "text";

/** Tipos de gráfico suportados por `<PlanilhaChart />`. */
export type TipoGrafico = "donut" | "bar" | "line" | "area";

/** Um ponto de série: o eixo X mais as chaves plotadas e os extras de tooltip. */
export type PontoGrafico = Record<string, string | number | null>;

// --------------------------------------------------------------------------
// Parâmetros (aba Config)
// --------------------------------------------------------------------------

export interface Assumptions {
  scenario: string;
  initial_capital: number;
  monthly_contribution: number;
  monthly_withdrawal: number;
  inflation_annual: number;
  selic_annual: number;
  real_rate_annual: number;
  real_rate_delta: number;
  duration_years: number;
  dy_fii: number;
  dy_stocks: number;
  price_return_stocks: number;
  price_return_fii: number;
  foreign_return: number;
  order_tolerance: number;
  start_date: string | null;
  end_date: string | null;
  target_monthly_real: number;
  tax_and_costs: number;
  monthly_income_target: number;
  small_cap_premium: number;
  dy_small_caps: number;
  dy_foreign: number;
}

// --------------------------------------------------------------------------
// Carteira
// --------------------------------------------------------------------------

export interface PositionResult {
  ticker: string;
  name: string;
  asset_class: string;
  quantity: number;
  price: number;
  market_value: number;
  portfolio_weight: number;
  proventos_period: number;
  var_12m: number | null;
  price_earnings: number | null;
  price_to_book: number | null;
  dividend_yield: number | null;
  technical_signal: string;
  fundamental_read: string;
  note: string;
}

export interface ClassSummary {
  asset_class: string;
  value: number;
  weight: number;
  positions: number;
}

export interface PortfolioResult {
  total_value: number;
  total_proventos: number;
  positions_count: number;
  weighted_var_12m: number;
  positions: PositionResult[];
  by_class: ClassSummary[];
}

// --------------------------------------------------------------------------
// Rebalanceamento
// --------------------------------------------------------------------------

export interface RebalanceRow {
  asset_class: string;
  current_value: number;
  current_weight: number;
  target_weight: number;
  band_min: number;
  band_max: number;
  /** "COMPRAR" | "VENDER" | "OK" (texto livre: o motor pode acrescentar rótulos). */
  action: string;
  deviation: number;
  amount: number;
}

/** Item de `rebalance.contribution_plan`. */
export interface ContributionPlanRow {
  asset_class: string;
  amount: number;
  share: number;
}

export interface RebalanceResult {
  rows: RebalanceRow[];
  total_value: number;
  targets_sum: number;
  band_relative: number;
  contribution_plan: ContributionPlanRow[];
  notes: string[];
}

// --------------------------------------------------------------------------
// Simulador
// --------------------------------------------------------------------------

export interface SimulationPoint {
  month: number;
  balance: number;
  invested: number;
  interest: number;
}

export interface SimulationResult {
  initial: number;
  monthly: number;
  annual_rate: number;
  monthly_rate: number;
  months: number;
  final_balance: number;
  total_invested: number;
  total_interest: number;
  series: SimulationPoint[];
}

// --------------------------------------------------------------------------
// Projeção
// --------------------------------------------------------------------------

export interface ProjectionPoint {
  /** Data ISO do mês de referência. */
  month: string;
  contribution: number;
  value_income: number;
  income_income: number;
  value_growth: number;
  income_growth: number;
  value_selected: number;
  income_selected: number;
}

export interface ProjectionResult {
  annual_return_income: number;
  annual_return_growth: number;
  scenario: string;
  series: ProjectionPoint[];
  final_value: number;
  final_income: number;
}

// --------------------------------------------------------------------------
// Cenários
// --------------------------------------------------------------------------

export interface ScenarioClass {
  name: string;
  weight: number;
  returns: Record<string, number>;
}

/** Item de `scenarios.inflation_forecast`. */
export interface InflationForecastRow {
  year: number;
  ipca: number;
  selic: number;
}

export interface ScenarioResult {
  classes: ScenarioClass[];
  returns_by_scenario: Record<string, number>;
  probabilities: Record<string, number>;
  expected_return: number;
  inflation_average: number;
  selic_average: number;
  weighted_dy_stocks: number;
  weighted_dy_fii: number;
  weighted_pe_stocks: number;
  weighted_pb_fii: number;
  inflation_forecast: InflationForecastRow[];
}

// --------------------------------------------------------------------------
// Fluxo de caixa
// --------------------------------------------------------------------------

export interface MonthlyCashflow {
  /** Data ISO do mês de referência. */
  month: string;
  inflow: number;
  expenses: number;
  invest_contribution: number;
  savings: number;
  invested_balance: number;
  cash_balance: number;
  net_worth: number;
}

/** Item de `cashflow.daily_balance`. */
export interface DailyBalanceRow {
  /** Data ISO do dia. */
  day: string;
  contribution: number;
  withdrawal: number;
  balance: number;
}

export interface CashflowResult {
  monthly: MonthlyCashflow[];
  daily_balance: DailyBalanceRow[];
  total_inflow: number;
  total_expenses: number;
  total_contributions: number;
  savings_rate: number;
  final_invested: number;
}

// --------------------------------------------------------------------------
// Proventos
// --------------------------------------------------------------------------

/** Item de `proventos.by_ticker` e de `proventos.top`. */
export interface ProventoPorTicker {
  ticker: string;
  label: string;
  product: string;
  amount: number;
  share: number;
  count: number;
  /** Data ISO do último pagamento (ou `null` quando a planilha não trouxe data). */
  last_paid_at: string | null;
}

/** Item de `proventos.by_kind`. */
export interface ProventoPorTipo {
  kind: string;
  raw_kinds: string[];
  amount: number;
  share: number;
  count: number;
}

/** Item de `proventos.by_month`. */
export interface ProventoPorMes {
  /** Data ISO do mês de referência. */
  month: string;
  label: string;
  amount: number;
  share: number;
  count: number;
}

export interface ProventosResult {
  total: number;
  by_ticker: ProventoPorTicker[];
  by_kind: ProventoPorTipo[];
  by_month: ProventoPorMes[];
  top: ProventoPorTicker[];
  period_start: string | null;
  period_end: string | null;
  monthly_average: number;
  yield_on_portfolio: number;
}

// --------------------------------------------------------------------------
// Consolidado + dolarizado
// --------------------------------------------------------------------------

/** Item de `consolidated.accounts`. */
export interface ConsolidatedAccountRow {
  institution: string;
  category: string;
  liquidity: string;
  value: number;
  share: number;
}

/** Item de `consolidated.by_category` / `by_liquidity` (o campo específico é opcional). */
export interface ConsolidatedGroup {
  label: string;
  value: number;
  share: number;
  accounts: number;
  category?: string;
  liquidity?: string;
}

/** Item de `consolidated.foreign`. */
export interface ForeignRow {
  broker: string;
  ticker: string;
  description: string;
  quantity: number;
  price_usd: number;
  value_usd: number;
  cost_usd: number;
  result_usd: number;
  result_percent: number;
  share: number;
  value_brl: number;
}

/** Item de `consolidated.foreign_by_broker`. */
export interface ForeignBrokerRow {
  broker: string;
  label: string;
  value_usd: number;
  value_brl: number;
  cost_usd: number;
  result_usd: number;
  result_percent: number;
  share: number;
  positions: number;
}

export interface ConsolidatedResult {
  total: number;
  accounts: ConsolidatedAccountRow[];
  by_category: ConsolidatedGroup[];
  by_liquidity: ConsolidatedGroup[];
  usd_brl: number;
  foreign_total_usd: number;
  foreign_total_brl: number;
  foreign: ForeignRow[];
  foreign_by_broker: ForeignBrokerRow[];
  foreign_result_usd: number;
}

// --------------------------------------------------------------------------
// Gráficos e KPIs
// --------------------------------------------------------------------------

/** Uma série plotada dentro de um gráfico (`{"key": "value", "label": "Valor (R$)"}`). */
export interface SerieGrafico {
  key: string;
  label: string;
}

export interface ChartSeries {
  id: string;
  title: string;
  kind: TipoGrafico;
  /** Chave do eixo X dentro de `data` (o motor usa sempre `"label"`). */
  x_key: string;
  series: SerieGrafico[];
  data: PontoGrafico[];
  value_format: FormatoValor;
  note: string;
}

/**
 * Um cartão da faixa de indicadores (`analysis.kpis`).
 *
 * Os campos são opcionais de propósito: o motor pode enviar `label` ou `title`,
 * `helper`/`hint`/`note`, e o valor já formatado (string) ou cru (número).
 */
export interface Kpi {
  id?: string;
  label?: string;
  title?: string;
  value?: number | string | null;
  format?: string;
  helper?: string;
  hint?: string;
  note?: string;
  /** "profit" | "loss" | "positive" | "negative" | "neutral" | "default". */
  tone?: string;
  icon?: string;
}

// --------------------------------------------------------------------------
// Resultado completo
// --------------------------------------------------------------------------

export interface Analysis {
  file_name: string;
  generated_at: string;
  source_kind: string;
  period_label: string;
  portfolio: PortfolioResult | null;
  rebalance: RebalanceResult | null;
  simulation: SimulationResult | null;
  projection: ProjectionResult | null;
  scenarios: ScenarioResult | null;
  cashflow: CashflowResult | null;
  proventos: ProventosResult | null;
  consolidated: ConsolidatedResult | null;
  charts: ChartSeries[];
  assumptions: Assumptions;
  kpis: Kpi[];
  warnings: string[];
  /** Metadados do motor (versão, tempo de execução, contadores). */
  engine: Record<string, string | number | boolean | null>;
}

/** Corpo de erro devolvido por `/api/py/planilha` (sempre em pt-BR). */
export interface ErroPlanilha {
  error: string;
}
