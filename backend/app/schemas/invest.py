from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

INVEST_DISCLAIMER = (
    "Educational information computed from public market data and the holdings "
    "you entered. Not investment advice. Investing involves risk of loss; past "
    "performance does not guarantee future results."
)


class DataQuality(BaseModel):
    days_used: int
    start: str | None = None
    end: str | None = None
    symbols_excluded: list[str] = []


# --- candles ---
# Declared as Literals so FastAPI rejects a bad value at the boundary with a
# 422 naming the allowed options, instead of it reaching the provider and
# coming back as a symbol-lookup error with irrelevant "add .TO" advice.
# Kept in step with marketdata.provider.VALID_INTERVALS / VALID_RANGES, which
# still re-check as defence in depth before anything is put in a URL.
CandleInterval = Literal["1m", "2m", "5m", "15m", "30m", "60m", "1d", "1wk"]
CandleRange = Literal["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]


class CandleOut(BaseModel):
    # ISO-8601 UTC. The client renders it in the exchange's timezone.
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


class CandleSeriesOut(BaseModel):
    symbol: str
    name: str | None = None
    currency: str
    interval: str
    range: str
    exchange_timezone: str | None = None
    market_state: str | None = None
    previous_close: float | None = None
    candles: list[CandleOut]
    # Free market data is delayed (commonly ~15 min for equities; crypto is
    # effectively live). Surfaced so the UI never implies a live tape.
    delayed: bool = True
    disclaimer: str = INVEST_DISCLAIMER


# --- holdings ---
class SymbolLookupOut(BaseModel):
    symbol: str
    name: str | None = None
    price: float
    currency: str


class WatchlistItemIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)


class WatchlistItemOut(BaseModel):
    id: str
    symbol: str
    name: str | None = None
    price: float | None = None
    currency: str = "USD"
    change_pct: float | None = None  # vs previous session close; None if unknown
    stale: bool = False  # true when a live quote could not be fetched


class HoldingIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    qty: float = Field(gt=0, le=1_000_000)
    avg_cost: float = Field(ge=0, le=1_000_000)  # per share, in the listing's currency


class HoldingOut(BaseModel):
    id: str
    symbol: str
    name: str | None = None
    qty: float
    avg_cost: float
    currency: str
    # Mirrored from a linked brokerage account — not hand-editable, since the
    # next sync would overwrite the change.
    is_synced: bool = False


class CashIn(BaseModel):
    cash: float = Field(ge=0, le=100_000_000)


# --- portfolio ---
class AccountOut(BaseModel):
    equity: float  # invested + cash, in CAD
    cash: float
    invested: float
    # What the positions cost, and what they are up or down against that — the
    # portfolio-level roll-up of the same numbers each position row carries.
    # All CAD; cash is excluded, since uninvested cash has no P/L.
    cost_basis: float = 0.0
    unrealized_pl: float = 0.0
    unrealized_pl_pct: float = 0.0
    # Move over the last cached session vs the one before it. ``None`` when no
    # position has two sessions cached — a brand-new portfolio has no
    # yesterday to compare against, which is not the same as a flat day.
    day_pl: float | None = None
    day_pl_pct: float | None = None


class PortfolioOut(BaseModel):
    connected: bool  # false = no holdings entered yet
    as_of: str | None = None
    # Date of the most recent daily bar the positions are priced from. Prices
    # come from the close cache, so on a trading day this trails ``as_of`` --
    # the UI labels the day change with this, never with "today".
    prices_as_of: str | None = None
    currency: str = "CAD"
    usd_cad_rate: float | None = None
    account: AccountOut | None = None
    positions: list[dict] = []
    stale_symbols: list[str] = []
    disclaimer: str = INVEST_DISCLAIMER


class SyncOut(BaseModel):
    symbols_updated: int
    bars_added: int
    data_start: str | None = None
    failed_symbols: list[str] = []


# --- risk ---
class PortfolioRisk(BaseModel):
    ann_vol: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    beta: float | None = None
    var_95: float | None = None
    var_99: float | None = None
    cvar_95: float | None = None
    var_95_amount: float | None = None  # CAD
    var_99_amount: float | None = None
    cvar_95_amount: float | None = None


class Concentration(BaseModel):
    hhi: float
    effective_n: float
    top_symbol: str | None = None
    top_weight: float | None = None


class PositionRisk(BaseModel):
    symbol: str
    weight: float
    ann_vol: float | None = None
    beta: float | None = None
    max_drawdown: float | None = None


class CorrelationOut(BaseModel):
    symbols: list[str]
    matrix: list[list[float]]


class FactorExposureOut(BaseModel):
    key: str
    name: str
    exposure: float
    variance_share: float
    contrib_vol: float
    standalone_vol: float
    unit: str | None = None
    duration_years: float | None = None


class FactorRiskOut(BaseModel):
    exposures: list[FactorExposureOut] = []
    factor_vol: float | None = None
    idiosyncratic_vol: float | None = None
    total_vol: float | None = None
    r_squared: float | None = None
    diversification_benefit: float | None = None
    factors_used: list[str] = []
    factors_unavailable: list[str] = []
    reason: str | None = None


class RiskOut(BaseModel):
    window: str
    portfolio: PortfolioRisk
    concentration: Concentration | None = None
    per_position: list[PositionRisk] = []
    correlation: CorrelationOut | None = None
    factors: FactorRiskOut | None = None
    risk_free_rate: float
    data_quality: DataQuality
    reason: str | None = None
    disclaimer: str = INVEST_DISCLAIMER


# --- dca ---
class AllocationIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    weight: float = Field(gt=0, le=1)


class DcaPlanIn(BaseModel):
    monthly_amount: float = Field(gt=0, le=1_000_000)
    allocations: list[AllocationIn] = Field(min_length=1, max_length=10)
    start_date: str
    expected_annual_return: float = Field(default=0.07, ge=-0.5, le=0.5)
    horizon_years: int = Field(default=10, ge=1, le=40)


class SeriesPoint(BaseModel):
    date: str
    value: float


class DcaBacktestOut(BaseModel):
    contributed: float
    final_value: float
    money_weighted_return: float | None = None
    series: list[SeriesPoint]
    contributed_series: list[SeriesPoint]


class DcaProjectionPoint(BaseModel):
    month: int
    value: float


class DcaPlanOut(BaseModel):
    backtest: DcaBacktestOut | None = None
    lump_sum_final_value: float | None = None
    lump_sum_note: str
    projection: list[DcaProjectionPoint]
    data_start: str | None = None
    data_quality: DataQuality | None = None
    disclaimer: str = INVEST_DISCLAIMER


# --- backtest ---
class BacktestIn(BaseModel):
    strategy: Literal["buy_hold", "dca", "sma_crossover", "fixed_mix"]
    symbols: list[str] = Field(min_length=1, max_length=10)
    start: str
    end: str | None = None
    fast: int = Field(default=50, ge=2, le=250)
    slow: int = Field(default=200, ge=5, le=500)
    weights: list[float] | None = None
    rebalance: Literal["monthly", "quarterly"] = "quarterly"
    monthly_amount: float = Field(default=500.0, gt=0)


class BacktestMetrics(BaseModel):
    cagr: float | None = None
    ann_vol: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    trades: int


class BacktestOut(BaseModel):
    strategy: str
    equity_curve: list[SeriesPoint]
    benchmark_curve: list[SeriesPoint]
    metrics: BacktestMetrics
    benchmark_metrics: BacktestMetrics
    data_start: str | None = None
    data_quality: DataQuality
    disclaimer: str = INVEST_DISCLAIMER


# --- projection ---
class ProjectionIn(BaseModel):
    horizon_years: Literal[1, 5, 10] = 5
    method: Literal["gbm", "bootstrap"] = "gbm"
    n_paths: int | None = Field(default=None, ge=100)
    monthly_contribution: float = Field(default=0.0, ge=0, le=1_000_000)
    seed: int | None = None


class ProjectionOut(BaseModel):
    start_value: float
    months: list[int]
    bands: dict[str, list[float]]
    prob_loss: float
    assumptions: dict
    framing: str = "Probabilistic projection from historical behavior — not a prediction."
    data_quality: DataQuality
    disclaimer: str = INVEST_DISCLAIMER


# --- scenarios ---
class ScenarioImpact(BaseModel):
    amount: float
    pct: float


class ScenarioPositionImpact(BaseModel):
    symbol: str
    impact: float
    shock_pct: float


class ScenarioOut(BaseModel):
    key: str
    name: str
    description: str
    source: str
    portfolio_impact: ScenarioImpact
    per_position: list[ScenarioPositionImpact]
    index_recovery: dict
    disclaimer: str = INVEST_DISCLAIMER


# --- targets + rebalance checklist ---
class TargetIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    # Zero is a real answer: "I hold this and I want none of it." Rejecting it
    # forced a held position to be omitted from the targets instead, which then
    # dropped it off the editor entirely — the same silence, harder to spot.
    weight: float = Field(ge=0, le=1)


class TargetsOut(BaseModel):
    targets: list[TargetIn]


class ProposedOrderOut(BaseModel):
    id: str
    batch_id: str
    kind: str
    symbol: str
    side: str
    qty: float | None = None
    # Dollar-sized orders carry an amount instead of a quantity. Exactly one of
    # `qty` / `notional` is set, and which one decides how the app phrases the
    # instruction: "buy 4 shares" or "put $120 in".
    notional: float | None = None
    est_value: float  # CAD
    reason: str
    status: str
    created_at: str
    # Present once the order has been routed to a real broker. status_detail
    # carries the broker's rejection reason on FAILED.
    broker_order_id: str | None = None
    status_detail: str | None = None


class ProposalOut(BaseModel):
    batch_id: str | None = None
    orders: list[ProposedOrderOut]
    total_buy: float
    total_sell: float
    note: str
    disclaimer: str = INVEST_DISCLAIMER


# How an order is sized. "shares" is whole shares — the only thing a broker can
# be handed through SnapTrade. "dollars" is a notional amount, which is how a
# fractional-share broker (Wealthsimple, Questrade) actually lets you buy, and
# the only sizing that works at all on an account too small for one share of
# what it wants. Notional orders cannot be routed, so they stay advisory.
Sizing = Literal["shares", "dollars"]


class RebalanceIn(BaseModel):
    drift_threshold: float = Field(default=0.05, ge=0.005, le=0.5)
    sizing: Sizing = "shares"


class MarkExecutedIn(BaseModel):
    # Actual fill price at the user's broker; defaults to the latest close.
    fill_price: float | None = Field(default=None, gt=0)


# --- deploy cash ("I have $X to invest") ---
class DeployPlanIn(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)
    # The whole universe is ranked for free on cached bars; only the top-ranked
    # survivors face the committee, and each one costs an LLM call. So this is
    # the knob that bounds both request latency and metered daily quota.
    max_candidates: int = Field(default=6, ge=1, le=12)
    # How many names the amount may be split across. Capped at the 10 that
    # `DeployAcceptIn` will accept as orders.
    max_positions: int = Field(default=5, ge=1, le=10)
    use_ai: bool = True
    sizing: Sizing = "shares"


class DeployPickOut(BaseModel):
    symbol: str
    name: str | None = None
    # Whole under "shares" sizing, fractional under "dollars" — in the latter
    # the amount is what was decided and the quantity is derived from it.
    qty: float
    price: float  # CAD per share
    est_value: float  # CAD
    weight: float  # share of `amount` this pick was allocated
    confidence: float
    rationale: str


class DeployPassedOut(BaseModel):
    """A candidate that was considered and not bought — shown so the user sees
    the reasoning, not just the picks."""

    symbol: str
    action: str  # SKIP | HOLD | unpriced | too-expensive
    confidence: float | None = None
    reason: str


class ResearchOut(BaseModel):
    """What the research layer contributed to a plan.

    Additive and optional: a client that ignores it sees exactly the response
    it saw before the layer existed. ``stale`` follows the same convention as
    ``PortfolioOut.stale_symbols`` — a degraded research layer is reported, not
    raised.
    """

    as_of: str
    macro_regime: str | None = None  # risk_on | risk_off | mixed
    macro_summary: str | None = None
    macro_headlines: list[str] = []
    symbols_with_news: int = 0
    stale: bool = False
    stale_reason: str | None = None


class DeployPlanOut(BaseModel):
    amount: float
    sizing: Sizing = "shares"
    picks: list[DeployPickOut]
    passed: list[DeployPassedOut]
    invested: float
    cash_remaining: float
    considered: int
    # ai = every ticker got a model verdict; mixed = some fell back to rules
    # mid-scan; rules = none did. Derived from the verdicts, not from a gate.
    source: str  # ai | mixed | rules
    note: str
    research: ResearchOut | None = None
    disclaimer: str = INVEST_DISCLAIMER


class DeployOrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    qty: float = Field(gt=0, le=1_000_000)
    price: float = Field(gt=0, le=1_000_000)
    reason: str = Field(default="", max_length=500)


class DeployAcceptIn(BaseModel):
    orders: list[DeployOrderIn] = Field(min_length=1, max_length=10)
    sizing: Sizing = "shares"


# --- AI portfolio actions ("what should I buy or sell?") ---
class ActionPlanIn(BaseModel):
    # Extra cash to put to work on top of the cash the app already tracks for
    # this account. Defaults to none — the plan spends what is already there.
    added_cash: float = Field(default=0.0, ge=0, le=10_000_000)
    # New names to open, on top of decisions about what is already held. Held
    # positions are always judged; this only bounds the shopping list.
    max_new_positions: int = Field(default=3, ge=0, le=6)
    # Committee calls are metered per day, so this bounds the shortlist of
    # unheld candidates that get one. Held positions each cost one too.
    max_candidates: int = Field(default=5, ge=1, le=10)
    use_ai: bool = True
    sizing: Sizing = "dollars"


class ActionOut(BaseModel):
    symbol: str
    name: str | None = None
    side: str  # buy | sell
    # EXIT and TRIM are sells, ADD tops up something held, NEW opens a position.
    action: str  # EXIT | TRIM | ADD | NEW
    amount: float  # CAD
    qty: float | None = None  # null when the order is sized in dollars
    price: float  # CAD per share
    current_weight: float
    projected_weight: float
    confidence: float
    reason: str


class ActionHoldOut(BaseModel):
    """Something held that the committee decided to leave alone — shown so the
    absence of an order reads as a decision rather than an oversight."""

    symbol: str
    weight: float
    confidence: float
    reason: str


class PlanFactorOut(BaseModel):
    """One factor, where the book sits today and where the plan would leave it."""

    key: str
    name: str
    exposure: float
    projected_exposure: float
    share_of_risk: float
    projected_share_of_risk: float
    duration_years: float | None = None
    projected_duration_years: float | None = None


class FactorBreachOut(BaseModel):
    """A limit the plan ran into.

    ``binding`` separates the two cases that look alike in a list and are not:
    the plan was cut back to stay inside the line, or the line is already
    crossed by holdings this plan cannot fix by buying less.
    """

    key: str
    name: str
    current: float
    projected: float
    limit: float
    unit: str  # exposure | share | years
    binding: bool
    message: str


class FactorPlanOut(BaseModel):
    factors: list[PlanFactorOut] = []
    idiosyncratic_share: float | None = None
    projected_idiosyncratic_share: float | None = None
    total_vol: float | None = None
    projected_total_vol: float | None = None
    breaches: list[FactorBreachOut] = []
    #: Whether a limit actually changed the plan, as opposed to merely warning.
    enforced: bool = False
    #: Why there is nothing here, when there is nothing here.
    reason: str | None = None

    def projected_exposure(self, key: str) -> float | None:
        return next((f.projected_exposure for f in self.factors if f.key == key), None)


class ActionPlanOut(BaseModel):
    as_of: str
    currency: str = "CAD"
    equity: float
    cash_before: float
    cash_after: float
    sizing: Sizing = "dollars"
    actions: list[ActionOut]
    holds: list[ActionHoldOut]
    passed: list[DeployPassedOut]
    considered: int
    source: str  # ai | mixed | rules
    note: str
    research: ResearchOut | None = None
    factors: FactorPlanOut | None = None
    disclaimer: str = INVEST_DISCLAIMER


class ActionOrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    side: Literal["buy", "sell"]
    amount: float = Field(gt=0, le=10_000_000)  # CAD
    price: float = Field(gt=0, le=1_000_000)
    qty: float | None = Field(default=None, gt=0, le=1_000_000)
    reason: str = Field(default="", max_length=500)


class ActionAcceptIn(BaseModel):
    orders: list[ActionOrderIn] = Field(min_length=1, max_length=20)
    sizing: Sizing = "dollars"


class ExplainerOut(BaseModel):
    title: str
    short: str
    body: str
    caveat: str
