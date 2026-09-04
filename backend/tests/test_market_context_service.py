"""Caching, coverage and degradation in the research layer.

The counters in these stubs are the point. Everything about this layer's cost
model is a claim about how many times a metered endpoint gets called, and a
claim like that is worth nothing unless something asserts it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.analytics.decision_context import (
    COVERAGE_COVERED,
    COVERAGE_ERROR,
    COVERAGE_FUND,
    COVERAGE_NONE,
)
from app.core.config import settings as app_settings
from app.integrations.news.provider import NewsArticle, NewsError
from app.integrations.websearch.provider import SearchResponse, SearchResult, WebSearchError
from app.services.market_context_service import MarketContextService


class StubNews:
    """Counts calls and answers from a script."""

    def __init__(self, script: dict[str, list[str]] | None = None, fail: set[str] | None = None):
        self.script = script or {}
        self.fail = fail or set()
        self.calls = 0
        self.asked: list[str] = []

    async def company_news(self, symbol, start, end, *, limit=8):
        self.calls += 1
        self.asked.append(symbol)
        if symbol in self.fail:
            raise NewsError(f"{symbol}: provider down")
        headlines = self.script.get(symbol, [])
        return [
            NewsArticle(
                published_at=datetime.now(UTC) - timedelta(hours=i + 1),
                headline=h,
                summary="",
                source="Reuters",
                url=f"https://example.test/{symbol}/{i}",
                article_id=f"{symbol}-{i}",
            )
            for i, h in enumerate(headlines)
        ]

    async def is_available(self):
        return True


class StubSearch:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    async def search(
        self, query, *, topic="news", days=2, max_results=8, depth="basic", include_answer=True
    ):
        self.calls += 1
        if self.fail:
            raise WebSearchError("search down")
        return SearchResponse(
            query=query,
            answer="Equities fell as inflation reaccelerated.",
            results=[
                SearchResult(
                    title="Stocks slump as inflation reaccelerates",
                    url="https://example.test/m1",
                    content="Indices closed lower.",
                    published_at=datetime.now(UTC),
                ),
                SearchResult(
                    title="Fed turns hawkish, warns on prices",
                    url="https://example.test/m2",
                    content="A longer hold.",
                    published_at=datetime.now(UTC),
                ),
            ],
        )

    async def is_available(self):
        return True


@pytest.fixture(autouse=True)
def _research_on(monkeypatch):
    monkeypatch.setattr(app_settings, "research_enabled", True)


# --- caching ----------------------------------------------------------------


async def test_the_macro_brief_is_fetched_once_a_day_for_the_whole_app(db_session):
    search = StubSearch()
    svc = MarketContextService(db_session, StubNews(), search)

    first = await svc.macro()
    second = await svc.macro()

    assert search.calls == 1, "the daily brief is the layer's main cost control"
    assert first is not None and second is not None
    assert second.regime == first.regime


async def test_a_symbols_news_is_fetched_once_a_day(db_session):
    news = StubNews({"AAPL": ["Apple ships a thing"]})
    svc = MarketContextService(db_session, news, StubSearch())

    await svc.for_symbols(["AAPL"])
    await svc.for_symbols(["AAPL"])

    assert news.calls == 1


async def test_no_coverage_is_cached_so_uncovered_tickers_are_not_re_asked(db_session):
    """The expensive mistake this table shape exists to prevent.

    Most TSX listings return nothing on the free tier. If "nothing" were not
    cacheable, every wide scan would re-ask about all of them, forever.
    """
    news = StubNews({"XIU.TO": []})
    svc = MarketContextService(db_session, news, StubSearch())

    first, _ = await svc.for_symbols(["XIU.TO"])
    assert first["XIU.TO"].coverage == COVERAGE_NONE

    second, _ = await svc.for_symbols(["XIU.TO"])
    assert news.calls == 1, "an empty answer is still an answer"
    assert second["XIU.TO"].coverage == COVERAGE_NONE


async def test_an_error_is_not_cached_so_the_next_run_retries(db_session):
    """A transient 500 must not lock in a day of silence for a symbol."""
    news = StubNews(fail={"AAPL"})
    svc = MarketContextService(db_session, news, StubSearch())

    first, stale = await svc.for_symbols(["AAPL"])
    assert first["AAPL"].coverage == COVERAGE_ERROR
    assert stale == ["AAPL"]

    await svc.for_symbols(["AAPL"])
    assert news.calls == 2, "errors must be retried, not remembered"


async def test_funds_are_answered_locally_and_never_cost_a_call(db_session):
    news = StubNews()
    svc = MarketContextService(db_session, news, StubSearch())

    out, _ = await svc.for_symbols(["XEQT.TO", "SPY", "VFV.TO"])

    assert news.calls == 0
    assert all(n.coverage == COVERAGE_FUND for n in out.values())


# --- degradation ------------------------------------------------------------


async def test_a_news_outage_surfaces_as_staleness_never_an_exception(db_session):
    news = StubNews(fail={"AAPL", "MSFT"})
    svc = MarketContextService(db_session, news, StubSearch())

    ctx = await svc.context(["AAPL", "MSFT"])

    assert ctx.stale is True
    assert set(ctx.stale_symbols) == {"AAPL", "MSFT"}
    assert ctx.macro is not None, "a news outage must not take the macro read with it"


async def test_a_search_outage_leaves_company_news_working(db_session):
    svc = MarketContextService(
        db_session, StubNews({"AAPL": ["Apple beats on earnings"]}), StubSearch(fail=True)
    )

    ctx = await svc.context(["AAPL"])

    assert ctx.macro is None
    assert ctx.for_symbol("AAPL").coverage == COVERAGE_COVERED
    assert ctx.stale is True


async def test_research_disabled_makes_zero_calls(db_session, monkeypatch):
    monkeypatch.setattr(app_settings, "research_enabled", False)
    news, search = StubNews(), StubSearch()
    svc = MarketContextService(db_session, news, search)

    ctx = await svc.context(["AAPL"])

    assert news.calls == 0 and search.calls == 0
    assert ctx.stale_reason == "research disabled"


async def test_a_missing_provider_degrades_rather_than_crashing(db_session):
    svc = MarketContextService(db_session, None, None)
    ctx = await svc.context(["AAPL"])
    assert ctx.macro is None
    assert ctx.for_symbol("AAPL").coverage == COVERAGE_ERROR


# --- the guardrail feed -----------------------------------------------------


async def test_a_fresh_guidance_cut_produces_a_guardrail_flag(db_session):
    news = StubNews({"ACME": ["Acme cuts full-year revenue guidance"]})
    svc = MarketContextService(db_session, news, StubSearch())

    flags = await svc.guardrail_flags(["ACME"])

    assert "ACME" in flags
    assert flags["ACME"].key == "guidance_cut"


async def test_a_bare_analyst_downgrade_does_not_reach_the_guardrail(db_session):
    """An opinion is not an event. Severity 1 informs the model; it does not act."""
    news = StubNews({"ACME": ["Analyst downgrades Acme to Hold"]})
    svc = MarketContextService(db_session, news, StubSearch())

    assert await svc.guardrail_flags(["ACME"]) == {}


async def test_an_uncovered_symbol_never_produces_a_guardrail_flag(db_session):
    news = StubNews({"XIU.TO": []})
    svc = MarketContextService(db_session, news, StubSearch())
    assert await svc.guardrail_flags(["XIU.TO"]) == {}


async def test_good_news_never_produces_a_flag(db_session):
    news = StubNews({"ACME": ["Acme raises full-year guidance"]})
    svc = MarketContextService(db_session, news, StubSearch())
    assert await svc.guardrail_flags(["ACME"]) == {}
