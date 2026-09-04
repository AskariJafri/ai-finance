"""Company-news provider interface.

Per-ticker headlines, used to answer "is something happening to this company
that the price has not caught up with yet". Deliberately narrow: no sentiment
score (the vendor's is a paid endpoint), no fundamentals, no filings.

Coverage is the thing to be careful about. A free tier that does not cover a
listing returns an empty array, which is indistinguishable from "this company
is having a quiet week" unless the caller keeps them apart — see
``app.analytics.decision_context.COVERAGE_NONE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from typing import Protocol, runtime_checkable


class NewsError(RuntimeError):
    """The provider could not be reached, or rejected the request.

    Never raised for "no articles" — that is an empty list and a normal answer.
    """


@dataclass(frozen=True)
class NewsArticle:
    published_at: datetime  # tz-aware UTC
    headline: str
    summary: str
    source: str  # publisher, e.g. "Reuters"
    url: str
    article_id: str  # vendor id, used to de-duplicate


# Caps on anything forwarded upstream, mirroring VALID_INTERVALS in the market
# data provider: request shapes are validated here, not wherever a caller
# happens to build them.
MAX_LOOKBACK_DAYS = 30
MAX_ARTICLES = 20


@runtime_checkable
class NewsProvider(Protocol):
    async def company_news(
        self, symbol: str, start: date_type, end: date_type, *, limit: int = 8
    ) -> list[NewsArticle]: ...

    async def is_available(self) -> bool: ...
