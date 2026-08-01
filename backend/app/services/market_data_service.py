"""Daily-bar cache: fetch incrementally from the market-data provider (Yahoo),
store in price_bars, serve aligned series to the analytics layer.

Bars are global market data shared across users. Incremental: each sync fetches
only dates after the latest cached bar per symbol. Symbols are Yahoo tickers,
so Canadian listings ("VFV.TO") work alongside US ones ("SPY").
"""

from __future__ import annotations

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


class MarketDataService:
    def __init__(self, session: AsyncSession, provider: MarketDataProvider) -> None:
        self.session = session
        self.provider = provider
        self.bars = PriceBarRepository(session)

    async def sync_bars(self, symbols: list[str]) -> tuple[int, int, list[str]]:
        """Fetch missing daily bars for symbols + benchmark + FX.

        Returns (symbols_updated, bars_added, failed_symbols). A bad ticker
        never fails the whole sync — it is reported back to the user.
        """
        today = date_type.today()
        wanted = sorted({s.upper() for s in symbols} | {BENCHMARK, FX_SYMBOL})
        symbols_updated = 0
        bars_added = 0
        failed: list[str] = []

        for symbol in wanted:
            latest = await self.bars.latest_date(symbol)
            start = (latest + timedelta(days=1)) if latest else HISTORY_START
            if start > today:
                continue
            try:
                points = await self.provider.get_bars(symbol, start, today)
            except MarketDataError as exc:
                logger.warning("Bar fetch failed for %s: %s", symbol, exc)
                failed.append(symbol)
                continue
            if not points:
                continue
            added = await self.bars.bulk_upsert(symbol, [(p.date, p.close) for p in points])
            if added:
                symbols_updated += 1
                bars_added += added

        return symbols_updated, bars_added, failed

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
