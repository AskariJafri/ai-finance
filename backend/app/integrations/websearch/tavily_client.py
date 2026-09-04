"""Tavily search — the macro/world read.

Free tier is ~1,000 credits a month, and this integration spends one credit a
day for the entire application, so the budget is not a real constraint.

``search_depth`` is pinned to "basic" by every caller: "advanced" takes five to
ten seconds, and the hosted deployment sits behind a 60-second gateway timeout
that a full plan already shares with market data and the model.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger
from app.integrations.websearch.provider import (
    MAX_RESULTS,
    VALID_DEPTHS,
    VALID_TOPICS,
    SearchResponse,
    SearchResult,
    WebSearchError,
)

logger = get_logger(__name__)


def _parse_published(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _to_result(row: dict) -> SearchResult | None:
    title = str(row.get("title") or "").strip()
    if not title:
        return None
    score = row.get("score")
    return SearchResult(
        title=title,
        url=str(row.get("url") or ""),
        content=str(row.get("content") or ""),
        published_at=_parse_published(row.get("published_date")),
        score=float(score) if isinstance(score, (int, float)) else None,
    )


class TavilySearch:
    def __init__(self, api_key: str, base_url: str, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _request(self, path: str, body: dict) -> dict:
        """The single HTTP seam — the one thing tests replace."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
                    resp = await client.post(f"{self.base_url}{path}", json=body)
                if resp.status_code in (401, 403):
                    raise WebSearchError("Tavily rejected the API key")
                if resp.status_code == 429 and attempt == 1:
                    await asyncio.sleep(1.5)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    raise WebSearchError("Tavily: unexpected response shape")
                return payload
            except WebSearchError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 2:
                    raise WebSearchError(f"Tavily: {exc}") from exc
                await asyncio.sleep(1.0)
        raise WebSearchError("Tavily: exhausted retries")

    async def search(
        self,
        query: str,
        *,
        topic: str = "news",
        days: int = 2,
        max_results: int = 8,
        depth: str = "basic",
        include_answer: bool = True,
    ) -> SearchResponse:
        if topic not in VALID_TOPICS:
            raise WebSearchError(f"unsupported topic {topic!r}")
        if depth not in VALID_DEPTHS:
            raise WebSearchError(f"unsupported depth {depth!r}")
        max_results = max(1, min(max_results, MAX_RESULTS))

        payload = await self._request(
            "/search",
            {
                "query": query,
                "topic": topic,
                "days": max(1, days),
                "max_results": max_results,
                "search_depth": depth,
                "include_answer": "basic" if include_answer else False,
            },
        )

        raw = payload.get("results")
        rows = raw if isinstance(raw, list) else []
        results = [r for r in (_to_result(x) for x in rows if isinstance(x, dict)) if r is not None]
        answer = payload.get("answer")
        return SearchResponse(
            query=query,
            answer=str(answer) if isinstance(answer, str) and answer.strip() else None,
            results=results,
        )

    async def is_available(self) -> bool:
        return bool(self.api_key)
