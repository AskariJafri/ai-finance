"""Deterministic market data for tests — closed-form curves, no network.

close(sym, t) = base * exp(mu*t + amp*sin(2*pi*t/63)) with t = weekday index
from _EPOCH, so any value is hand-verifiable.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from datetime import date as date_type

from app.integrations.marketdata.provider import (
    VALID_INTERVALS,
    VALID_RANGES,
    BarPoint,
    Candle,
    CandleSeries,
    MarketDataError,
    SymbolQuote,
)

# Minutes per candle, for the intervals the mock synthesizes.
_INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1,
    "2m": 2,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "1d": 60 * 24,
    "1wk": 60 * 24 * 7,
}
_RANGE_DAYS: dict[str, int] = {
    "1d": 1,
    "5d": 5,
    "1mo": 30,
    "3mo": 90,
    "6mo": 180,
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "max": 3650,
}
# Cap so a "max"/1m request can't synthesize millions of candles in a test.
_MAX_MOCK_CANDLES = 400

_EPOCH = date_type(2016, 1, 4)  # a Monday

# symbol -> (base, daily drift, seasonal amplitude, currency)
CURVES: dict[str, tuple[float, float, float, str]] = {
    "AAPL": (25.0, 0.00095, 0.05, "USD"),
    "MSFT": (50.0, 0.00090, 0.04, "USD"),
    "BND": (80.0, 0.00008, 0.01, "USD"),
    "SPY": (200.0, 0.00045, 0.03, "USD"),
    "VFV.TO": (60.0, 0.00050, 0.03, "CAD"),
    "XEQT.TO": (20.0, 0.00040, 0.03, "CAD"),
    "ZAG.TO": (15.0, 0.00007, 0.01, "CAD"),
    "USDCAD=X": (1.30, 0.00002, 0.005, "CAD"),
}


def _t_index(d: date_type) -> int:
    full_weeks, rem = divmod((d - _EPOCH).days, 7)
    return full_weeks * 5 + min(rem, 4)


def mock_close(symbol: str, d: date_type) -> float:
    base, mu, amp, _ = CURVES[symbol]
    t = _t_index(d)
    return base * math.exp(mu * t + amp * math.sin(2 * math.pi * t / 63))


class MockMarketData:
    async def get_bars(self, symbol: str, start: date_type, end: date_type) -> list[BarPoint]:
        if symbol not in CURVES:
            raise MarketDataError(f"{symbol}: unknown ticker")
        bars = []
        d = max(start, _EPOCH)
        while d <= end:
            if d.weekday() < 5:
                bars.append(BarPoint(date=d, close=mock_close(symbol, d)))
            d += timedelta(days=1)
        return bars

    async def get_quote(self, symbol: str) -> SymbolQuote:
        if symbol not in CURVES:
            raise MarketDataError(f"{symbol}: unknown ticker")
        return SymbolQuote(
            symbol=symbol,
            price=mock_close(symbol, date_type.today()),
            currency=CURVES[symbol][3],
            name=f"Mock {symbol}",
        )

    async def get_candles(self, symbol: str, interval: str, range_: str) -> CandleSeries:
        """Deterministic OHLC. The daily close curve sets the candle's close and
        a fixed intrabar shape derives O/H/L from it, so every value is
        reproducible and high >= max(open, close) >= min(open, close) >= low
        holds by construction."""
        if symbol not in CURVES:
            raise MarketDataError(f"{symbol}: unknown ticker")
        if interval not in VALID_INTERVALS:
            raise MarketDataError(f"unsupported interval {interval!r}")
        if range_ not in VALID_RANGES:
            raise MarketDataError(f"unsupported range {range_!r}")

        step = timedelta(minutes=_INTERVAL_MINUTES[interval])
        count = min(
            _MAX_MOCK_CANDLES,
            max(1, int(timedelta(days=_RANGE_DAYS[range_]) / step)),
        )
        end = datetime.now(tz=UTC).replace(second=0, microsecond=0)

        candles: list[Candle] = []
        for i in range(count - 1, -1, -1):
            at = end - step * i
            close = mock_close(symbol, at.date())
            # Fixed wobble keyed off the bar index — no randomness, so tests can
            # assert exact values.
            wobble = math.sin(i / 3.0)
            open_ = close * (1 + 0.004 * wobble)
            high = max(open_, close) * 1.003
            low = min(open_, close) * 0.997
            candles.append(
                Candle(
                    time=at,
                    open=round(open_, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close, 4),
                    volume=100_000 + i * 13,
                )
            )

        return CandleSeries(
            symbol=symbol,
            currency=CURVES[symbol][3],
            interval=interval,
            candles=candles,
            name=f"Mock {symbol}",
            exchange_timezone="America/New_York",
            market_state="CLOSED",
            previous_close=candles[0].open if candles else None,
        )
