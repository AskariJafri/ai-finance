"""Yahoo Finance chart API — free, keyless, global.

Works from Canada and prices Canadian listings natively (VFV.TO -> CAD) as
well as US ones (SPY -> USD), with adjusted closes going back decades. This
is an unofficial endpoint: it needs a browser User-Agent, and we degrade
gracefully (MarketDataError) rather than failing user requests.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from datetime import date as date_type

import httpx

from app.core.logging import get_logger
from app.integrations.marketdata.provider import (
    VALID_INTERVALS,
    VALID_RANGES,
    BarPoint,
    Candle,
    CandleSeries,
    MarketDataError,
    SymbolQuote,
)

logger = get_logger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# Yahoo blocks requests without a browser-like UA.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _at(values: list, i: int):
    """Yahoo's OHLC arrays are usually parallel to ``timestamp`` but occasionally
    come back short; treat a missing index as a null rather than raising."""
    return values[i] if i < len(values) else None


class YahooMarketData:
    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    async def _chart(self, symbol: str, params: dict) -> dict:
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, headers=HEADERS) as client:
                    resp = await client.get(CHART_URL.format(symbol=symbol), params=params)
                if resp.status_code == 429 and attempt == 1:
                    await asyncio.sleep(1.5)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 2:
                    raise MarketDataError(f"{symbol}: {exc}") from exc
                await asyncio.sleep(1.0)

        chart = data.get("chart") or {}
        if chart.get("error"):
            raise MarketDataError(f"{symbol}: {chart['error'].get('description', 'unknown')}")
        results = chart.get("result") or []
        if not results:
            raise MarketDataError(f"{symbol}: no data (check the ticker)")
        return results[0]

    async def get_bars(self, symbol: str, start: date_type, end: date_type) -> list[BarPoint]:
        result = await self._chart(
            symbol,
            {
                "period1": int(datetime.combine(start, datetime.min.time(), UTC).timestamp()),
                "period2": int(datetime.combine(end, datetime.max.time(), UTC).timestamp()),
                "interval": "1d",
                "events": "div,split",
            },
        )
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        adj = (indicators.get("adjclose") or [{}])[0].get("adjclose")
        closes = adj or (indicators.get("quote") or [{}])[0].get("close") or []

        bars: list[BarPoint] = []
        # strict=False on purpose: Yahoo occasionally returns a close series
        # shorter than the timestamp series, and truncating is preferable to
        # raising on a live market-data response.
        for ts, close in zip(timestamps, closes, strict=False):
            if close is None:
                continue  # Yahoo emits nulls for halted/holiday sessions
            bars.append(
                BarPoint(
                    date=datetime.fromtimestamp(ts, tz=UTC).date(),
                    close=float(close),
                )
            )
        return bars

    async def get_candles(self, symbol: str, interval: str, range_: str) -> CandleSeries:
        """OHLC candles. Same endpoint ``get_bars`` uses — the open/high/low and
        volume arrays are already in that payload, they were simply discarded.

        Not real-time: this free feed is typically delayed (commonly ~15 minutes
        for equities; crypto is effectively live). ``market_state`` is passed
        through so the UI can say so rather than implying a live tape.
        """
        if interval not in VALID_INTERVALS:
            raise MarketDataError(f"unsupported interval {interval!r}")
        if range_ not in VALID_RANGES:
            raise MarketDataError(f"unsupported range {range_!r}")

        result = await self._chart(
            symbol,
            {"range": range_, "interval": interval, "includePrePost": "false"},
        )
        meta = result.get("meta") or {}
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]

        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        candles: list[Candle] = []
        for i, ts in enumerate(timestamps):
            o, h, low, c = (
                _at(opens, i),
                _at(highs, i),
                _at(lows, i),
                _at(closes, i),
            )
            # Yahoo emits nulls for halted/illiquid intervals. A partial candle
            # would put a null through the client's SVG path maths, so drop it.
            if None in (o, h, low, c):
                continue
            candles.append(
                Candle(
                    time=datetime.fromtimestamp(ts, tz=UTC),
                    open=float(o),
                    high=float(h),
                    low=float(low),
                    close=float(c),
                    volume=int(_at(volumes, i) or 0),
                )
            )

        return CandleSeries(
            symbol=(meta.get("symbol") or symbol).upper(),
            currency=(meta.get("currency") or "USD").upper(),
            interval=interval,
            candles=candles,
            name=meta.get("longName") or meta.get("shortName"),
            exchange_timezone=meta.get("exchangeTimezoneName"),
            market_state=meta.get("marketState"),
            previous_close=(
                float(meta["chartPreviousClose"])
                if meta.get("chartPreviousClose") is not None
                else None
            ),
        )

    async def get_quote(self, symbol: str) -> SymbolQuote:
        result = await self._chart(symbol, {"range": "5d", "interval": "1d"})
        meta = result.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            raise MarketDataError(f"{symbol}: no price available")
        return SymbolQuote(
            symbol=meta.get("symbol", symbol).upper(),
            price=float(price),
            currency=(meta.get("currency") or "USD").upper(),
            name=meta.get("longName") or meta.get("shortName"),
        )
