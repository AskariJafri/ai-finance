from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.news.provider import NewsProvider

logger = get_logger(__name__)


@lru_cache
def get_news_provider() -> NewsProvider:
    if settings.use_mock_news:
        from app.integrations.news.mock_client import MockNews

        logger.info("Using MockNews (no FINNHUB_API_KEY or NEWS_USE_MOCK set)")
        return MockNews()

    from app.integrations.news.finnhub_client import FinnhubNews

    logger.info("Using FinnhubNews")
    return FinnhubNews(
        api_key=settings.finnhub_api_key,
        base_url=settings.finnhub_base_url,
        timeout=settings.finnhub_timeout_seconds,
    )
