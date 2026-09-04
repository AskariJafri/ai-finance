"""Web-search provider interface, used for the macro/world read.

Scope is narrow on purpose: one query a day for the whole application, asking
what is happening in markets generally. This is not a per-ticker research tool
— that is what the news provider is for.

``answer`` is a provider-generated synthesis. It is convenient, it is free
inside the same search credit, and it is third-party text like any other
result: it gets sanitized and labelled untrusted before it reaches a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


class WebSearchError(RuntimeError):
    """The search provider could not be reached, or rejected the request."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    content: str  # snippet
    published_at: datetime | None = None
    score: float | None = None


@dataclass(frozen=True)
class SearchResponse:
    query: str
    answer: str | None = None
    results: list[SearchResult] = field(default_factory=list)


# Allow-lists for anything forwarded upstream.
VALID_TOPICS = ("general", "news", "finance")
VALID_DEPTHS = ("basic", "advanced")
MAX_RESULTS = 12


@runtime_checkable
class WebSearchProvider(Protocol):
    async def search(
        self,
        query: str,
        *,
        topic: str = "news",
        days: int = 2,
        max_results: int = 8,
        depth: str = "basic",
        include_answer: bool = True,
    ) -> SearchResponse: ...

    async def is_available(self) -> bool: ...
