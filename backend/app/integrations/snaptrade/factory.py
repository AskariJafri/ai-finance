"""Selects/builds the SnapTrade adapter.

Mirrors the Plaid factory, with one extra safeguard: the real client is used
only when credentials exist. Whether *orders* may actually be placed is a
separate check (``settings.live_trading_enabled``) enforced in the service
layer, so a read-only linked account never becomes a trading one by accident.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.snaptrade.gateway import SnapTradeGateway

logger = get_logger(__name__)


@lru_cache
def get_snaptrade_gateway() -> SnapTradeGateway:
    if settings.use_mock_snaptrade:
        from app.integrations.snaptrade.mock_client import MockSnapTradeClient

        logger.info("Using MockSnapTradeClient (no credentials or SNAPTRADE_USE_MOCK set)")
        return MockSnapTradeClient()

    from app.integrations.snaptrade.real_client import RealSnapTradeClient

    logger.info(
        "Using RealSnapTradeClient",
        extra={"extra": {"live_trading": settings.live_trading_enabled}},
    )
    return RealSnapTradeClient()
