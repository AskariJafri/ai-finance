"""Daily-bar cache: fetch incrementally from the market-data provider (Yahoo),
store in price_bars, serve aligned series to the analytics layer.

Bars are global market data shared across users. Incremental: each sync fetches
only dates after the latest cached bar per symbol. Symbols are Yahoo tickers,
so Canadian listings ("VFV.TO") work alongside US ones ("SPY").
"""

from __future__ import annotations

import asyncio
from datetime import date as date_type
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.marketdata.provider import (
    CandleSeries,
    MarketDataError,
    MarketDataProvider,
    SymbolQuote,
)
from app.repositories.price_bars import PriceBarRepository

logger = get_logger(__name__)

BENCHMARK = "SPY"
FX_SYMBOL = "USDCAD=X"  # Yahoo's USD/CAD ticker
HISTORY_START = date_type(2010, 1, 1)

# Upstream fetches run concurrently; the DB writes that follow do not (see
# `sync_bars`). Kept modest so a wide scan doesn't look like a scraper to Yahoo.
MAX_CONCURRENT_FETCHES = 8


class MarketDataService:
    def __init__(self, session: AsyncSession, provider: MarketDataProvider) -> None:
        self.session = session
        self.provider = provider
        self.bars = PriceBarRepository(session)

    async def sync_bars(
        self, symbols: list[str], since: date_type | None = None
    ) -> tuple[int, int, list[str]]:
        """Fetch missing daily bars for symbols + benchmark + FX.

        `since` is how far back the caller needs history. It defaults to
        `HISTORY_START` (what backtests and Monte Carlo want). The cash-deploy
        scanner passes a much shorter window instead: it ranks a universe far
        wider than any one portfolio, and pulling fifteen years for every
        candidate would cost more than the scan is worth.

        Fetches run concurrently, writes do not — an `AsyncSession` is not safe
        for concurrent use, so the network phase fans out and the upsert phase
        stays sequential.

        Returns (symbols_updated, bars_added, failed_symbols). A bad ticker
        never fails the whole sync — it is reported back to the user.
        """
        today = date_type.today()
        want_start = since or HISTORY_START
        wanted = sorted({s.upper() for s in symbols} | {BENCHMARK, FX_SYMBOL})

        jobs: list[tuple[str, date_type, date_type]] = []
        for symbol in wanted:
            jobs.extend(await self._missing_ranges(symbol, want_start, today))
        if not jobs:
            return 0, 0, []

        limiter = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

        async def fetch(
            symbol: str, start: date_type, end: date_type
        ) -> tuple[str, list[tuple[date_type, float]], bool]:
            async with limiter:
                try:
                    points = await self.provider.get_bars(symbol, start, end)
                except MarketDataError as exc:
                    logger.warning("Bar fetch failed for %s: %s", symbol, exc)
                    return symbol, [], True
                return symbol, [(p.date, p.close) for p in points], False

        fetched = await asyncio.gather(*(fetch(*job) for job in jobs))

        updated: set[str] = set()
        failed: list[str] = []
        bars_added = 0
        for symbol, points, error in fetched:
            if error:
                if symbol not in failed:
                    failed.append(symbol)
                continue
            if not points:
                continue
            added = await self.bars.bulk_upsert(symbol, points)
            if added:
                updated.add(symbol)
                bars_added += added

        return len(updated), bars_added, failed

    async def _missing_ranges(
        self, symbol: str, want_start: date_type, today: date_type
    ) -> list[tuple[str, date_type, date_type]]:
        """Date ranges we don't already have cached for `symbol`.

        Both ends matter. Forward-filling alone was enough while every caller
        started at `HISTORY_START`; now that a caller may ask for a short
        window, a symbol first cached by that caller would otherwise be stuck
        with a truncated history no later backtest could repair — so an earlier
        `want_start` backfills.
        """
        earliest = await self.bars.earliest_date(symbol)
        latest = await self.bars.latest_date(symbol)
        if earliest is None or latest is None:
            return [(symbol, want_start, today)] if want_start <= today else []

        ranges: list[tuple[str, date_type, date_type]] = []
        if want_start < earliest:
            ranges.append((symbol, want_start, earliest - timedelta(days=1)))
        if latest < today:
            ranges.append((symbol, latest + timedelta(days=1), today))
        return ranges

    async def lookup(self, symbol: str) -> SymbolQuote:
        """Validate a ticker and get its live price + currency (for holdings)."""
        return await self.provider.get_quote(symbol.upper())

    async def get_candles(self, symbol: str, interval: str, range_: str) -> CandleSeries:
        """OHLC candles for the chart. Deliberately NOT cached in price_bars:
        that table is daily adjusted closes for analytics, while these are raw
        intraday bars that go stale within the minute — different data with a
        different lifetime, so mixing them would corrupt the backtest series."""
        return await self.provider.get_candles(symbol.upper(), interval, range_)

    async def latest_close(self, symbol: str) -> float | None:
        latest = await self.bars.latest_date(symbol.upper())
        if latest is None:
            return None
        series = await self.bars.get_series(symbol.upper(), start=latest, end=latest)
        return series[0][1] if series else None

    async def latest_fx_rate(self) -> float | None:
        """USD -> CAD."""
        return await self.latest_close(FX_SYMBOL)

    async def get_close_series(
        self,
        symbols: list[str],
        start: date_type | None = None,
        end: date_type | None = None,
    ) -> dict[str, list[tuple[date_type, float]]]:
        out = {}
        for symbol in symbols:
            out[symbol.upper()] = await self.bars.get_series(symbol.upper(), start=start, end=end)
        return out

    async def data_start(self, symbols: list[str]) -> date_type | None:
        """First date on which ALL symbols have data (honest backtest clamp)."""
        earliest: list[date_type] = []
        for symbol in symbols:
            d = await self.bars.earliest_date(symbol.upper())
            if d is None:
                return None
            earliest.append(d)
        return max(earliest) if earliest else None
