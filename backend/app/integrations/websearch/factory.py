from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.websearch.provider import WebSearchProvider

logger = get_logger(__name__)


@lru_cache
def get_websearch_provider() -> WebSearchProvider:
    if settings.use_mock_websearch:
        from app.integrations.websearch.mock_client import MockWebSearch

        logger.info("Using MockWebSearch (no TAVILY_API_KEY or WEBSEARCH_USE_MOCK set)")
        return MockWebSearch()

    from app.integrations.websearch.tavily_client import TavilySearch

    logger.info("Using TavilySearch")
    return TavilySearch(
        api_key=settings.tavily_api_key,
        base_url=settings.tavily_base_url,
        timeout=settings.tavily_timeout_seconds,
    )
