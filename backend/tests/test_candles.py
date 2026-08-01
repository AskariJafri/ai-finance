"""OHLC candle series: provider parsing, validation, and the API surface."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.integrations.marketdata.mock_client import MockMarketData
from app.integrations.marketdata.provider import MarketDataError
from app.integrations.marketdata.yahoo_client import YahooMarketData

PREFIX = "/api/v1"


async def _register(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post(
        f"{PREFIX}/auth/register", json={"email": email, "password": "supersecret123"}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# --- mock provider ---


@pytest.mark.asyncio
async def test_mock_candles_are_internally_consistent():
    series = await MockMarketData().get_candles("AAPL", "5m", "1d")
    assert series.candles
    for c in series.candles:
        # The invariant a candlestick renderer relies on; violating it would
        # draw a wick inside the body.
        assert c.high >= max(c.open, c.close)
        assert c.low <= min(c.open, c.close)
        assert c.high >= c.low


@pytest.mark.asyncio
async def test_mock_candles_are_chronological():
    series = await MockMarketData().get_candles("AAPL", "5m", "1d")
    times = [c.time for c in series.candles]
    assert times == sorted(times)


@pytest.mark.asyncio
async def test_mock_candles_reject_unknown_symbol():
    with pytest.raises(MarketDataError):
        await MockMarketData().get_candles("NOPE", "5m", "1d")


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,range_", [("7m", "1d"), ("5m", "7d"), ("", "1d")])
async def test_mock_candles_reject_bad_interval_or_range(interval, range_):
    with pytest.raises(MarketDataError):
        await MockMarketData().get_candles("AAPL", interval, range_)


@pytest.mark.asyncio
async def test_mock_candle_count_is_capped():
    """A max/1m request must not synthesize millions of bars."""
    series = await MockMarketData().get_candles("AAPL", "1m", "max")
    assert len(series.candles) <= 400


# --- Yahoo response parsing (no network) ---


def _yahoo_payload(timestamps, quote, meta=None):
    return {
        "meta": {
            "symbol": "AAPL",
            "currency": "USD",
            "exchangeTimezoneName": "America/New_York",
            "marketState": "REGULAR",
            "chartPreviousClose": 100.0,
            **(meta or {}),
        },
        "timestamp": timestamps,
        "indicators": {"quote": [quote]},
    }


@pytest.mark.asyncio
async def test_yahoo_parses_ohlc(monkeypatch):
    client = YahooMarketData()
    payload = _yahoo_payload(
        [1_700_000_000, 1_700_000_300],
        {
            "open": [10.0, 11.0],
            "high": [12.0, 13.0],
            "low": [9.0, 10.5],
            "close": [11.0, 12.5],
            "volume": [1000, 2000],
        },
    )

    async def fake_chart(self, symbol, params):
        return payload

    monkeypatch.setattr(YahooMarketData, "_chart", fake_chart)

    series = await client.get_candles("AAPL", "5m", "1d")
    assert series.symbol == "AAPL"
    assert series.currency == "USD"
    assert series.market_state == "REGULAR"
    assert series.previous_close == 100.0
    assert len(series.candles) == 2
    first = series.candles[0]
    assert (first.open, first.high, first.low, first.close) == (10.0, 12.0, 9.0, 11.0)
    assert first.volume == 1000
    assert first.time == datetime.fromtimestamp(1_700_000_000, tz=UTC)


@pytest.mark.asyncio
async def test_yahoo_drops_null_candles(monkeypatch):
    """Yahoo emits nulls for halted/illiquid intervals. A partial candle would
    put a null into the client's SVG maths, so it must be dropped whole."""
    payload = _yahoo_payload(
        [1, 2, 3],
        {
            "open": [10.0, None, 12.0],
            "high": [11.0, None, 13.0],
            "low": [9.0, None, 11.0],
            "close": [10.5, None, 12.5],
            "volume": [100, None, 300],
        },
    )

    async def fake_chart(self, symbol, params):
        return payload

    monkeypatch.setattr(YahooMarketData, "_chart", fake_chart)

    series = await YahooMarketData().get_candles("AAPL", "5m", "1d")
    assert len(series.candles) == 2
    assert all(c.close is not None for c in series.candles)


@pytest.mark.asyncio
async def test_yahoo_tolerates_short_ohlc_arrays(monkeypatch):
    """Arrays occasionally come back shorter than `timestamp`; that must not
    raise an IndexError mid-parse."""
    payload = _yahoo_payload(
        [1, 2, 3],
        {"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5], "volume": [100]},
    )

    async def fake_chart(self, symbol, params):
        return payload

    monkeypatch.setattr(YahooMarketData, "_chart", fake_chart)

    series = await YahooMarketData().get_candles("AAPL", "5m", "1d")
    assert len(series.candles) == 1


@pytest.mark.asyncio
async def test_yahoo_rejects_bad_interval_before_network(monkeypatch):
    """Validation must happen before the request goes out — these values are
    forwarded to a third-party URL."""
    called = False

    async def fake_chart(self, symbol, params):
        nonlocal called
        called = True
        return _yahoo_payload([], {})

    monkeypatch.setattr(YahooMarketData, "_chart", fake_chart)

    with pytest.raises(MarketDataError):
        await YahooMarketData().get_candles("AAPL", "3m", "1d")
    assert called is False


# --- API ---


@pytest.mark.asyncio
async def test_candles_endpoint(client: AsyncClient):
    headers = await _register(client, "candles1@test.com")
    resp = await client.get(
        f"{PREFIX}/invest/candles",
        headers=headers,
        params={"symbol": "AAPL", "interval": "5m", "range": "1d"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["interval"] == "5m"
    assert body["range"] == "1d"
    assert body["delayed"] is True  # never imply a live tape on free data
    assert body["candles"]
    c = body["candles"][0]
    assert set(c) == {"time", "open", "high", "low", "close", "volume"}


@pytest.mark.asyncio
async def test_candles_endpoint_rejects_bad_interval(client: AsyncClient):
    headers = await _register(client, "candles2@test.com")
    resp = await client.get(
        f"{PREFIX}/invest/candles",
        headers=headers,
        params={"symbol": "AAPL", "interval": "3m", "range": "1d"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_candles_endpoint_rejects_unknown_symbol(client: AsyncClient):
    headers = await _register(client, "candles3@test.com")
    resp = await client.get(
        f"{PREFIX}/invest/candles",
        headers=headers,
        params={"symbol": "NOTREAL", "interval": "5m", "range": "1d"},
    )
    assert resp.status_code == 422
    assert ".TO" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_candles_endpoint_requires_auth(client: AsyncClient):
    resp = await client.get(f"{PREFIX}/invest/candles", params={"symbol": "AAPL"})
    assert resp.status_code in (401, 403)
