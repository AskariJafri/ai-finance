"""Finnhub company news.

Free tier, 60 requests/minute, no card. Coverage is strongest on US listings;
Canadian ``.TO`` tickers are thin to absent, which is why the caller treats an
empty result as its own outcome rather than as good news.

The API key travels in the ``X-Finnhub-Token`` header rather than the query
string, so it cannot end up in a logged URL.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from datetime import date as date_type

import httpx

from app.core.logging import get_logger
from app.integrations.news.provider import (
    MAX_ARTICLES,
    MAX_LOOKBACK_DAYS,
    NewsArticle,
    NewsError,
)

logger = get_logger(__name__)


def _articles(payload: object) -> list[dict]:
    """Unwrap a response body into a list of raw article dicts.

    Finnhub answers ``/company-news`` with a bare JSON array, but returns a
    ``{"error": "..."}`` *object* for some rejections while still sending 200.
    Parsing the object as a sequence would silently yield zero articles, which
    reads downstream as "no news" — the exact confusion this integration is
    built to avoid. Same lesson as the SnapTrade ``{"results": [...]}`` change.
    """
    if isinstance(payload, dict):
        message = payload.get("error") or "unexpected object response"
        raise NewsError(f"Finnhub: {message}")
    if not isinstance(payload, list):
        raise NewsError(f"Finnhub: unexpected response type {type(payload).__name__}")
    return [row for row in payload if isinstance(row, dict)]


def _to_article(row: dict) -> NewsArticle | None:
    """Map one raw row, or ``None`` to drop it.

    Dropping beats raising: one malformed row in a batch of twenty should cost
    that row, not the whole symbol's news.
    """
    headline = str(row.get("headline") or "").strip()
    if not headline:
        return None
    stamp = row.get("datetime")
    if not isinstance(stamp, (int, float)) or stamp <= 0:
        # Finnhub sends 0 for undated wire copy. An article we cannot date
        # cannot be aged, and freshness is what the guardrail turns on.
        return None
    try:
        published_at = datetime.fromtimestamp(float(stamp), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return NewsArticle(
        published_at=published_at,
        headline=headline,
        summary=str(row.get("summary") or ""),
        source=str(row.get("source") or "unknown"),
        url=str(row.get("url") or ""),
        article_id=str(row.get("id") or row.get("url") or headline),
    )


class FinnhubNews:
    def __init__(self, api_key: str, base_url: str, timeout: float = 12.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _request(self, path: str, params: dict) -> object:
        """The single HTTP seam. Everything network lives here so tests have
        exactly one thing to replace."""
        headers = {"X-Finnhub-Token": self.api_key, "Accept": "application/json"}
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
                    resp = await client.get(f"{self.base_url}{path}", params=params)
                if resp.status_code in (401, 403):
                    # A rejected key is not transient; retrying wastes the
                    # caller's latency budget to be told the same thing.
                    raise NewsError("Finnhub rejected the API key")
                if resp.status_code == 429 and attempt == 1:
                    await asyncio.sleep(1.5)
                    continue
                resp.raise_for_status()
                return resp.json()
            except NewsError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 2:
                    raise NewsError(f"Finnhub: {exc}") from exc
                await asyncio.sleep(1.0)
        raise NewsError("Finnhub: exhausted retries")

    async def company_news(
        self, symbol: str, start: date_type, end: date_type, *, limit: int = 8
    ) -> list[NewsArticle]:
        if (end - start).days > MAX_LOOKBACK_DAYS:
            raise NewsError(f"lookback exceeds {MAX_LOOKBACK_DAYS} days")
        limit = max(1, min(limit, MAX_ARTICLES))
        payload = await self._request(
            "/company-news",
            {"symbol": symbol.upper(), "from": start.isoformat(), "to": end.isoformat()},
        )
        rows = _articles(payload)
        mapped = [a for a in (_to_article(r) for r in rows) if a is not None]
        mapped.sort(key=lambda a: a.published_at, reverse=True)
        return mapped[:limit]

    async def is_available(self) -> bool:
        return bool(self.api_key)
