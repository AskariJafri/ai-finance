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


class PortfolioOut(BaseModel):
    connected: bool  # false = no holdings entered yet
    as_of: str | None = None
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


class RiskOut(BaseModel):
    window: str
    portfolio: PortfolioRisk
    concentration: Concentration | None = None
    per_position: list[PositionRisk] = []
    correlation: CorrelationOut | None = None
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
    weight: float = Field(gt=0, le=1)


class TargetsOut(BaseModel):
    targets: list[TargetIn]


class ProposedOrderOut(BaseModel):
    id: str
    batch_id: str
    kind: str
    symbol: str
    side: str
    qty: float | None = None
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


class RebalanceIn(BaseModel):
    drift_threshold: float = Field(default=0.05, ge=0.005, le=0.5)


class MarkExecutedIn(BaseModel):
    # Actual fill price at the user's broker; defaults to the latest close.
    fill_price: float | None = Field(default=None, gt=0)


# --- deploy cash ("I have $X to invest") ---
class DeployPlanIn(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)
    # Only the top-ranked survivors face the committee — each one costs six LLM
    # calls, so this is the knob that bounds how long the request takes.
    max_candidates: int = Field(default=4, ge=1, le=6)
    use_ai: bool = True


class DeployPickOut(BaseModel):
    symbol: str
    name: str | None = None
    qty: int  # whole shares only — fractional orders can't be routed to a broker
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


class DeployPlanOut(BaseModel):
    amount: float
    picks: list[DeployPickOut]
    passed: list[DeployPassedOut]
    invested: float
    cash_remaining: float
    considered: int
    source: str  # ai | rules
    note: str
    disclaimer: str = INVEST_DISCLAIMER


class DeployOrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    qty: int = Field(gt=0, le=1_000_000)
    price: float = Field(gt=0, le=1_000_000)
    reason: str = Field(default="", max_length=500)


class DeployAcceptIn(BaseModel):
    orders: list[DeployOrderIn] = Field(min_length=1, max_length=10)


class ExplainerOut(BaseModel):
    title: str
    short: str
    body: str
    caveat: str
