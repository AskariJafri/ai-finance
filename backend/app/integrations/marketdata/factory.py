from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.integrations.marketdata.mock_client import MockMarketData
from app.integrations.marketdata.provider import MarketDataProvider
from app.integrations.marketdata.yahoo_client import YahooMarketData


@lru_cache
def get_market_data_provider() -> MarketDataProvider:
    if settings.market_data_use_mock:
        return MockMarketData()
    return YahooMarketData()
