from __future__ import annotations

from pydantic import BaseModel, Field

_DISCLAIMER = (
    "Paper trading with simulated money — no real orders are placed. Prices are "
    "delayed public market data, converted to CAD. Educational only."
)


class PaperPositionOut(BaseModel):
    symbol: str
    qty: float
    avg_price: float  # CAD per share
    price: float | None  # live, CAD
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    opened_by: str


class PaperAccountOut(BaseModel):
    cash: float
    invested: float
    equity: float  # cash + invested
    unrealized_pl: float
    currency: str = "CAD"


class PaperPortfolioOut(BaseModel):
    account: PaperAccountOut
    positions: list[PaperPositionOut]
    stale_symbols: list[str] = []
    disclaimer: str = _DISCLAIMER


class TradeIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    qty: float = Field(gt=0, le=1_000_000)


class PaperTradeOut(BaseModel):
    id: str
    symbol: str
    side: str  # buy | sell
    qty: float
    price: float
    source: str
    note: str | None = None
    created_at: str


class AutoTraderConfigOut(BaseModel):
    enabled: bool
    dry_run: bool
    max_positions: int
    per_trade: float  # CAD
    universe: list[str]


class AutoTraderConfigIn(BaseModel):
    enabled: bool | None = None
    dry_run: bool | None = None
    max_positions: int | None = Field(default=None, ge=1, le=20)
    per_trade: float | None = Field(default=None, gt=0, le=1_000_000)
    universe: list[str] | None = None


class AutoTradeDecisionOut(BaseModel):
    symbol: str
    action: str  # buy | sell | hold | skip
    reason: str
    score: float | None = None
    executed: bool = False


class AutoTradeRunOut(BaseModel):
    id: str
    dry_run: bool
    scanned: int
    executed: int
    summary: str
    decisions: list[AutoTradeDecisionOut]
    created_at: str
