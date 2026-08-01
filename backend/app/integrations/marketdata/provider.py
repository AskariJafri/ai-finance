"""Market-data provider protocol + DTOs.

Chosen over a brokerage data feed because it must work from Canada, needs no
API key, and must price Canadian listings (e.g. VFV.TO in CAD) as well as US
ones — none of which a US-only brokerage feed does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from typing import Protocol


class MarketDataError(RuntimeError):
    """Provider rejected the request or the symbol is unknown."""


@dataclass(frozen=True)
class BarPoint:
    date: date_type
    close: float  # split/dividend adjusted


@dataclass(frozen=True)
class SymbolQuote:
    symbol: str
    price: float
    currency: str  # "USD" | "CAD"
    name: str | None = None


# --- candlesticks ---

# (interval, default range) pairs the UI offers. Constrained to an allow-list
# rather than passed through from the client: these are forwarded to an
# upstream HTTP API, and the provider rejects combinations it can't serve
# (1-minute data, for instance, only goes back about a week).
VALID_INTERVALS: tuple[str, ...] = ("1m", "2m", "5m", "15m", "30m", "60m", "1d", "1wk")
VALID_RANGES: tuple[str, ...] = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max")


@dataclass(frozen=True)
class Candle:
    """One OHLC bar. ``time`` is timezone-aware UTC."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


@dataclass(frozen=True)
class CandleSeries:
    symbol: str
    currency: str
    interval: str
    candles: list[Candle] = field(default_factory=list)
    name: str | None = None
    # Exchange-local timezone, so the client can label times the way the
    # exchange does rather than in the phone's timezone.
    exchange_timezone: str | None = None
    # "REGULAR" | "PRE" | "POST" | "CLOSED" — lets the UI say whether what it's
    # showing is still moving.
    market_state: str | None = None
    previous_close: float | None = None


class MarketDataProvider(Protocol):
    async def get_bars(self, symbol: str, start: date_type, end: date_type) -> list[BarPoint]: ...

    async def get_quote(self, symbol: str) -> SymbolQuote: ...

    async def get_candles(self, symbol: str, interval: str, range_: str) -> CandleSeries: ...
