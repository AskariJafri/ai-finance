"""Deterministic macro search for tests and keyless local runs.

The tape is deliberately **balanced** — two negative headlines, two positive —
so ``read_regime`` returns "mixed".

That balance matters more than it looks. Macro context is injected into every
ticker's prompt identically, so a permanently risk-off mock makes the committee
decline every candidate on every run, and a keyless local install looks broken
rather than cautious. Regime *detection* is asserted in test_news_signals.py
against its own tapes, which is where a decisive one belongs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.integrations.websearch.provider import (
    MAX_RESULTS,
    VALID_DEPTHS,
    VALID_TOPICS,
    SearchResponse,
    SearchResult,
    WebSearchError,
)

_HEADLINES = [
    ("Stocks slump as inflation reaccelerates", "Broad selloff across major indices."),
    ("Fed turns hawkish, warns on sticky core prices", "Officials signalled a longer hold."),
    ("Tech rebounds as earnings beat estimates", "Megacap results came in ahead of plan."),
    ("Energy gains as crude rallies", "One of the stronger sectors."),
]


class MockWebSearch:
    def __init__(self) -> None:
        self.calls = 0

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
        # Same validation as the real client.
        if topic not in VALID_TOPICS:
            raise WebSearchError(f"unsupported topic {topic!r}")
        if depth not in VALID_DEPTHS:
            raise WebSearchError(f"unsupported depth {depth!r}")
        max_results = max(1, min(max_results, MAX_RESULTS))
        self.calls += 1

        now = datetime.now(UTC)
        results = [
            SearchResult(
                title=title,
                url=f"https://example.test/macro/{i}",
                content=content,
                published_at=now - timedelta(hours=3 * i),
                score=1.0 - (i * 0.1),
            )
            for i, (title, content) in enumerate(_HEADLINES[:max_results])
        ]
        answer = (
            (
                "A mixed session: inflation data ran hot and the Fed signalled a longer "
                "hold, while megacap earnings beat and energy outperformed."
            )
            if include_answer
            else None
        )
        return SearchResponse(query=query, answer=answer, results=results)

    async def is_available(self) -> bool:
        return True
