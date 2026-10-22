export type TokenPair = {
  access_token: string;
  refresh_token: string;
  token_type: string;
};

export type User = {
  id: string;
  email: string;
  full_name: string | null;
};

export type CategoryGroup = "income" | "expense" | "transfer";
export type CategorySource = "uncategorized" | "plaid" | "rule" | "ai" | "user";

export type Category = {
  id: string;
  name: string;
  slug: string;
  group: CategoryGroup;
  icon: string | null;
  color: string | null;
  is_system: boolean;
  budget_bucket: "needs" | "wants" | null;
};

export type Transaction = {
  id: string;
  name: string;
  merchant_name: string | null;
  amount: number; // positive = outflow, negative = inflow
  currency: string;
  date: string;
  pending: boolean;
  category: Category | null;
  category_source: CategorySource;
  category_confidence: number;
  // Mirrors backend/app/repositories/transactions.py's guards — see
  // src/utils/totals.ts, which is the client-side copy of those predicates.
  is_transfer: boolean;
  account_type: string | null;
};

export type Account = {
  id: string;
  name: string;
  official_name: string | null;
  mask: string | null;
  type: string | null;
  subtype: string | null;
  currency: string;
  current_balance: number;
  available_balance: number | null;
};

export type CategorySpend = {
  category: string;
  color: string | null;
  amount: number;
};

export type DashboardSummary = {
  period_start: string;
  period_end: string;
  period_label: string;
  // Net worth: cash minus card/loan debt. Kept as "total_balance" for API
  // stability — see cash_balance/credit_balance for what it's made of.
  total_balance: number;
  cash_balance: number;
  credit_balance: number;
  period_spending: number;
  period_income: number;
  net_cash_flow: number;
  savings_rate: number;
  uncategorized_count: number;
  spend_by_category: CategorySpend[];
  recent_transactions: Transaction[];
};

export type LinkTokenResponse = { link_token: string; expiration: string | null };

export type ExchangeResponse = {
  item_id: string;
  institution_name: string | null;
  accounts_synced: number;
  transactions_synced: number;
};

export type SyncResponse = {
  added: number;
  modified: number;
  removed: number;
  accounts_synced: number;
  categorized_by_ai: number;
};

export type PlaidStatus = {
  configured: boolean;
  source: "user" | "environment" | "mock";
  plaid_env: string;
  client_id_masked: string | null;
};

export type PlaidCredentialInput = {
  client_id: string;
  secret: string;
  plaid_env: "sandbox" | "production";
};

// --- Reports / 50-30-20 / Advice ---
export type MonthReport = {
  month: string; // "YYYY-MM"
  income: number; // salary only, counted in the month of the payday it was paid against
  other_income: number; // refunds, benefits, gifts, one-off deposits
  spending: number;
  net: number; // income + other_income - spending
  savings_rate: number;
  by_category: CategorySpend[];
};

export type MonthlyReportResponse = { months: MonthReport[] };

export type BucketStat = {
  amount: number; // savings may be negative
  share: number; // of income, 0 when no income
  target: number; // 0.5 / 0.3 / 0.2
  target_amount: number; // target x income, in dollars
  // target_amount - amount. For needs/wants this is headroom left to spend;
  // for savings (a residual, not a spend bucket) it reads the opposite way —
  // a shortfall against the goal. Same field, presentation picks the wording.
  remaining: number;
};

export type BudgetRuleReport = {
  month: string;
  // Salary. Every target below is a percentage of this, which is why
  // other_income sits beside it rather than inside it — a one-off deposit
  // shouldn't raise the bar for what you're allowed to spend.
  income: number;
  detected_income: number;
  other_income: number;
  income_source: "detected" | "override";
  needs: BucketStat;
  wants: BucketStat;
  savings: BucketStat;
  uncategorized_amount: number;
};

export type Advice = {
  month: string;
  content: string | null;
  source: "ai" | "fallback" | "none";
  model: string | null;
  generated_at: string | null;
  disclaimer: string;
};

export type BudgetSettings = {
  monthly_income_override: number | null;
};

// --- Invest ---
export type SymbolLookup = {
  symbol: string;
  name: string | null;
  price: number;
  currency: string;
};

export type Holding = {
  id: string;
  symbol: string;
  name: string | null;
  qty: number;
  avg_cost: number;
  currency: string;
  /** Mirrored from a linked brokerage account — not editable by hand. */
  is_synced: boolean;
};

// --- candles ---
export type Candle = {
  time: string; // ISO-8601 UTC
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
};

export type CandleSeries = {
  symbol: string;
  name: string | null;
  currency: string;
  interval: string;
  range: string;
  exchange_timezone: string | null;
  market_state: string | null; // REGULAR | PRE | POST | CLOSED
  previous_close: number | null;
  candles: Candle[];
  /** Free market data is delayed — never render this as a live tape. */
  delayed: boolean;
  disclaimer: string;
};

export type WatchlistItem = {
  id: string;
  symbol: string;
  name: string | null;
  price: number | null;
  currency: string;
  change_pct: number | null;
  stale: boolean;
};

// --- AI Committee ---
export type AgentTake = {
  agent: string;
  name: string;
  stance: string; // bullish | bearish | neutral | caution
  confidence: number;
  thesis: string;
};

export type CommitteeResult = {
  id: string | null;
  symbol: string;
  price: number | null;
  currency: string;
  action: string; // BUY | SKIP | HOLD
  confidence: number;
  summary: string;
  source: string; // ai | rules
  agents: AgentTake[];
  indicators: Record<string, number | string | null>;
  created_at: string | null;
  disclaimer: string;
};

export type CommitteeSession = {
  id: string;
  symbol: string;
  action: string;
  confidence: number;
  source: string;
  created_at: string;
};

// --- Paper trading ---
export type PaperPosition = {
  symbol: string;
  qty: number;
  avg_price: number;
  price: number | null;
  market_value: number;
  unrealized_pl: number;
  unrealized_pl_pct: number;
  opened_by: string;
};

export type PaperAccount = {
  cash: number;
  invested: number;
  equity: number;
  unrealized_pl: number;
  currency: string;
};

export type PaperPortfolio = {
  account: PaperAccount;
  positions: PaperPosition[];
  stale_symbols: string[];
  disclaimer: string;
};

export type PaperTrade = {
  id: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  source: string;
  note: string | null;
  created_at: string;
};

// --- Auto-trader ---
export type AutoTraderConfig = {
  enabled: boolean;
  dry_run: boolean;
  max_positions: number;
  per_trade: number;
  universe: string[];
};

export type AutoTradeDecision = {
  symbol: string;
  action: string; // buy | sell | hold | skip
  reason: string;
  score: number | null;
  executed: boolean;
};

export type AutoTradeRun = {
  id: string;
  dry_run: boolean;
  scanned: number;
  executed: number;
  summary: string;
  decisions: AutoTradeDecision[];
  created_at: string;
};

export type DataQuality = {
  days_used: number;
  start: string | null;
  end: string | null;
  symbols_excluded: string[];
};

export type Position = {
  symbol: string;
  name: string | null;
  qty: number;
  currency: string;
  /** True when the row is mirrored from a linked brokerage account. */
  is_synced: boolean;
  price: number;
  /** Date of the close `price` came from — bars, so it can trail today. */
  price_date: string;
  avg_cost: number;
  cost_basis: number; // CAD
  market_value: number; // CAD
  unrealized_pl: number;
  unrealized_pl_pct: number;
  /** Prior session's close, in the listing currency. Null = no prior bar. */
  prev_close: number | null;
  /** Day-over-day move per share, in the listing currency. */
  day_change: number | null;
  day_change_pct: number | null;
  /** The position's share of the day's move, in CAD. */
  day_pl: number | null;
  weight: number;
};

export type Portfolio = {
  connected: boolean; // false = no holdings entered yet
  as_of: string | null;
  /** Latest close date behind the prices — what the day change is measured on. */
  prices_as_of: string | null;
  currency: string; // "CAD"
  usd_cad_rate: number | null;
  account: {
    equity: number;
    cash: number;
    invested: number;
    cost_basis: number;
    unrealized_pl: number;
    unrealized_pl_pct: number;
    /** Null when no position has a prior session cached — unknown, not flat. */
    day_pl: number | null;
    day_pl_pct: number | null;
  } | null;
  positions: Position[];
  stale_symbols: string[];
  disclaimer: string;
};

export type InvestSyncResponse = {
  symbols_updated: number;
  bars_added: number;
  data_start: string | null;
  failed_symbols: string[];
};

export type RiskReport = {
  window: string;
  portfolio: {
    ann_vol: number | null;
    sharpe: number | null;
    sortino: number | null;
    max_drawdown: number | null;
    beta: number | null;
    var_95: number | null;
    var_99: number | null;
    cvar_95: number | null;
    var_95_amount: number | null;
    var_99_amount: number | null;
    cvar_95_amount: number | null;
    var_95_amount_cad: number | null;
  };
  concentration: {
    hhi: number;
    effective_n: number;
    top_symbol: string | null;
    top_weight: number | null;
  } | null;
  per_position: {
    symbol: string;
    weight: number;
    ann_vol: number | null;
    beta: number | null;
    max_drawdown: number | null;
  }[];
  correlation: { symbols: string[]; matrix: number[][] } | null;
  risk_free_rate: number;
  data_quality: DataQuality;
  reason: string | null;
  disclaimer: string;
};

export type SeriesPoint = { date: string; value: number };

export type BacktestRequest = {
  strategy: "buy_hold" | "dca" | "sma_crossover" | "fixed_mix";
  symbols: string[];
  start: string;
  end?: string;
  fast?: number;
  slow?: number;
  weights?: number[];
  rebalance?: "monthly" | "quarterly";
  monthly_amount?: number;
};

export type BacktestMetrics = {
  cagr: number | null;
  ann_vol: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  trades: number;
};

export type BacktestResponse = {
  strategy: string;
  equity_curve: SeriesPoint[];
  benchmark_curve: SeriesPoint[];
  metrics: BacktestMetrics;
  benchmark_metrics: BacktestMetrics;
  data_start: string | null;
  data_quality: DataQuality;
  disclaimer: string;
};

export type DcaPlanRequest = {
  monthly_amount: number;
  allocations: { symbol: string; weight: number }[];
  start_date: string;
  expected_annual_return?: number;
  horizon_years?: number;
};

export type DcaPlanResponse = {
  backtest: {
    contributed: number;
    final_value: number;
    money_weighted_return: number | null;
    series: SeriesPoint[];
    contributed_series: SeriesPoint[];
  } | null;
  lump_sum_final_value: number | null;
  lump_sum_note: string;
  projection: { month: number; value: number }[];
  data_start: string | null;
  disclaimer: string;
};

export type ProjectionRequest = {
  horizon_years: 1 | 5 | 10;
  method: "gbm" | "bootstrap";
  n_paths?: number;
  monthly_contribution?: number;
  seed?: number;
};

export type ProjectionResponse = {
  start_value: number;
  months: number[];
  bands: { p5: number[]; p25: number[]; p50: number[]; p75: number[]; p95: number[] };
  prob_loss: number;
  assumptions: {
    mu_ann: number;
    sigma_ann: number;
    method: string;
    n_paths: number;
    window_days: number;
  };
  framing: string;
  data_quality: DataQuality;
  disclaimer: string;
};

export type Scenario = {
  key: string;
  name: string;
  description: string;
  source: string;
  portfolio_impact: { amount: number; pct: number };
  per_position: { symbol: string; impact: number; shock_pct: number }[];
  index_recovery: { index: string; months: number | null; note: string };
  disclaimer: string;
};

export type Target = { symbol: string; weight: number };

export type ProposedOrder = {
  id: string;
  batch_id: string;
  kind: string;
  symbol: string;
  side: "buy" | "sell";
  /** Set under share sizing; null when the order is sized in dollars. */
  qty: number | null;
  /** Set under dollar sizing; null when the order is sized in shares. */
  notional: number | null;
  est_value: number; // CAD
  reason: string;
  status: string;
  created_at: string;
  // Set once routed to a real broker; status_detail carries the rejection
  // reason when status is "failed".
  broker_order_id: string | null;
  status_detail: string | null;
};

// --- Brokerage (SnapTrade) ---
export type BrokerageLink = {
  portal_url: string;
  disclaimer: string;
};

export type BrokerageConnection = {
  id: string;
  brokerage_account_id: string;
  account_name: string | null;
  institution_name: string | null;
  number_masked: string | null;
  currency: string;
  cash: number;
  total_value: number;
  /** False when the broker granted read-only access or live trading is off. */
  trading_enabled: boolean;
  status: string;
  last_synced_at: string | null;
};

export type BrokerageSyncResponse = {
  positions_synced: number;
  positions_removed: number;
  cash_cents: number;
  /** Accounts the broker could not be reached for; the rest still synced. */
  failed_accounts: string[];
};

export type Proposal = {
  batch_id: string | null;
  orders: ProposedOrder[];
  total_buy: number;
  total_sell: number;
  note: string;
  disclaimer: string;
};

// --- deploy cash ("I have $X to invest") ---
/**
 * How a plan is sized. "shares" is whole shares — the only thing that can be
 * routed to a broker. "dollars" is a notional amount, which is how a
 * fractional-share broker (Wealthsimple, Questrade) actually lets you buy, and
 * the only sizing that works on an account too small for one share.
 */
export type Sizing = "shares" | "dollars";

export type DeployPlanRequest = {
  amount: number;
  max_candidates?: number; // how many finalists face the committee (1-12)
  max_positions?: number; // how many names the amount is split across (1-10)
  use_ai?: boolean;
  sizing?: Sizing;
};

export type DeployPick = {
  symbol: string;
  name: string | null;
  qty: number; // whole under "shares" sizing, fractional under "dollars"
  price: number; // CAD per share
  est_value: number; // CAD
  weight: number; // share of the amount allocated
  confidence: number;
  rationale: string;
};

export type DeployPassed = {
  symbol: string;
  action: string; // SKIP | HOLD | unpriced | too-expensive | not-shortlisted
  confidence: number | null;
  reason: string;
};

export type DeployPlan = {
  amount: number;
  sizing: Sizing;
  picks: DeployPick[];
  passed: DeployPassed[];
  invested: number;
  cash_remaining: number;
  considered: number;
  source: "ai" | "rules";
  note: string;
  disclaimer: string;
};

export type DeployAcceptRequest = {
  orders: { symbol: string; qty: number; price: number; reason: string }[];
  sizing?: Sizing;
};

// --- AI portfolio actions (sell + buy over what you already own) ---
export type ActionPlanRequest = {
  /** Money being added on top of the cash the app already tracks. */
  added_cash?: number;
  max_new_positions?: number; // new names to open (0-6)
  max_candidates?: number; // unheld tickers that get a committee call (1-10)
  use_ai?: boolean;
  sizing?: Sizing;
};

export type PortfolioAction = {
  symbol: string;
  name: string | null;
  side: "buy" | "sell";
  /** EXIT and TRIM are sells; ADD tops up a holding, NEW opens a position. */
  action: "EXIT" | "TRIM" | "ADD" | "NEW";
  amount: number; // CAD
  qty: number | null; // null when sized in dollars
  price: number; // CAD per share
  current_weight: number;
  projected_weight: number;
  confidence: number;
  reason: string;
};

/** Something held that the plan deliberately leaves alone. */
export type ActionHold = {
  symbol: string;
  weight: number;
  confidence: number;
  reason: string;
};

/** One factor, where the book sits now and where the plan would leave it. */
export type PlanFactor = {
  key: string;
  name: string;
  exposure: number;
  projected_exposure: number;
  /** Share of the portfolio's factor risk, not of its value. */
  share_of_risk: number;
  projected_share_of_risk: number;
  duration_years: number | null;
  projected_duration_years: number | null;
};

export type FactorBreach = {
  key: string;
  name: string;
  current: number;
  projected: number;
  limit: number;
  unit: "exposure" | "share" | "years";
  /**
   * true: the plan was cut back to stay inside this line.
   * false: the line is already crossed by holdings, and buying less would not
   * bring it back — a warning, not something this plan did.
   */
  binding: boolean;
  message: string;
};

/** What kind of risk the plan leaves behind, and which house limits it met. */
export type FactorPlan = {
  factors: PlanFactor[];
  idiosyncratic_share: number | null;
  projected_idiosyncratic_share: number | null;
  total_vol: number | null;
  projected_total_vol: number | null;
  breaches: FactorBreach[];
  /** Whether a limit actually changed the plan, as opposed to only warning. */
  enforced: boolean;
  /** Why there is nothing here, when there is nothing here. */
  reason: string | null;
};

export type ActionPlan = {
  as_of: string;
  currency: string;
  equity: number;
  cash_before: number;
  cash_after: number;
  sizing: Sizing;
  actions: PortfolioAction[];
  holds: ActionHold[];
  passed: DeployPassed[];
  considered: number;
  /** "mixed" means some verdicts fell back to rules when the model was down. */
  source: "ai" | "mixed" | "rules";
  /** Why the AI did not answer everything, in one line; null when it did. */
  ai_note?: string | null;
  note: string;
  factors: FactorPlan | null;
  disclaimer: string;
};

export type ActionAcceptRequest = {
  orders: {
    symbol: string;
    side: "buy" | "sell";
    amount: number;
    price: number;
    qty?: number | null;
    reason: string;
  }[];
  sizing?: Sizing;
};

export type Explainer = {
  title: string;
  short: string;
  body: string;
  caveat: string;
};

// --- Finance chat ---

// "ai": the model answered. "glossary": the model was unreachable and a
// reviewed explainer matched. "fallback": neither — say so plainly.
export type ChatAnswerSource = "ai" | "glossary" | "fallback";

export type ChatMessage = {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  source: ChatAnswerSource | null;
  model: string | null;
  context_used: string[];
  created_at: string;
};

export type ChatSession = {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
};

export type ChatSendResponse = {
  session_id: string;
  user_message: ChatMessage;
  reply: ChatMessage;
  disclaimer: string;
};

export type ChatSuggestion = {
  label: string;
  prompt: string;
  group: "money" | "invest" | "learn";
};

export type LLMEndpointStatus = {
  label: string;
  model: string | null;
  state: string; // ok | quota | rate_limited | model_gone | bad_key | transport
  retry_in: number;
};

export type ChatStatus = {
  ai_enabled: boolean;
  llm_available: boolean;
  model: string | null;
  endpoints?: LLMEndpointStatus[];
  suggestions: ChatSuggestion[];
  disclaimer: string;
};
