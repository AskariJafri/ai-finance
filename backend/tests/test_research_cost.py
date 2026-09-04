"""The cost contract.

Everything about the research layer's design is a claim about how many times a
metered endpoint gets called. The claims are: a wide scan asks for news only
about its finalists, the macro read costs one search a day for the whole
application, and the LLM call count does not change at all.

Those are cheap to break by accident — hoisting a fetch a few lines earlier, or
"helpfully" pre-loading news during ranking, would each turn a bounded K into
an unbounded N without failing any other test. Hence the counters.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings as app_settings
from app.integrations.marketdata.provider import BarPoint, SymbolQuote
from app.integrations.news.provider import NewsArticle
from app.repositories.users import UserRepository
from app.schemas.invest import DeployPlanIn
from app.services import deploy_service
from app.services.deploy_service import DeployService
from app.services.market_context_service import MarketContextService
from tests.test_autotrader_real import _series
from tests.test_market_context_service import StubSearch

# A pool big enough that "K, not N" is a meaningful distinction. Deliberately
# names that are not in FUND_SYMBOLS, so none is answered locally and the
# tiering is what keeps the count down rather than the fund shortcut.
SIXTY_FIVE = [f"SYM{i:02d}" for i in range(65)]


class CountingNews:
    def __init__(self):
        self.calls = 0
        self.asked: list[str] = []

    async def company_news(self, symbol, start, end, *, limit=8):
        self.calls += 1
        self.asked.append(symbol)
        return [
            NewsArticle(
                published_at=datetime.now(UTC) - timedelta(hours=3),
                headline=f"{symbol} trading update",
                summary="",
                source="Reuters",
                url="https://example.test/x",
                article_id=f"{symbol}-0",
            )
        ]

    async def is_available(self):
        return True


class RisingAll:
    """Every symbol is a clean uptrend, so ranking cannot trim the pool for us."""

    async def get_quote(self, symbol):
        return SymbolQuote(symbol.upper(), 100.0, "USD", f"{symbol.upper()} Inc")

    async def get_bars(self, symbol, start, end):
        if symbol.upper() == "USDCAD=X":
            return [BarPoint(start + timedelta(days=i), 1.4) for i in range(200)]
        return [BarPoint(start + timedelta(days=i), c) for i, c in enumerate(_series(100.0, 0.5))]


class CountingLLM:
    model = "fake"

    def __init__(self):
        self.calls = 0

    async def complete(self, prompt, *, system=None, temperature=0.0, json_object=False):
        self.calls += 1
        # Deliberately unusable: the verdict degrades to rules per ticker, which
        # is fine here — this file counts calls, it does not judge answers.
        return "{}"

    async def is_available(self):
        return True


@pytest.fixture(autouse=True)
def _research_on(monkeypatch):
    monkeypatch.setattr(app_settings, "research_enabled", True)
    monkeypatch.setattr(app_settings, "market_data_use_mock", False)
    monkeypatch.setattr(deploy_service, "DEPLOY_UNIVERSE", SIXTY_FIVE)


async def _user(session) -> uuid.UUID:
    u = await UserRepository(session).create(
        email=f"cost{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    return u.id


@pytest.mark.asyncio
async def test_a_sixty_five_ticker_scan_asks_for_news_about_six(seeded_session):
    news, search, llm = CountingNews(), StubSearch(), CountingLLM()
    svc = DeployService(
        seeded_session,
        RisingAll(),
        llm,
        MarketContextService(seeded_session, news, search),
    )
    user_id = await _user(seeded_session)

    plan = await svc.plan(user_id, DeployPlanIn(amount=5_000, max_candidates=6))

    assert plan.considered >= 60, "the rules stage really did rank the whole pool"
    assert news.calls <= 6, f"news must follow the shortlist, not the pool (asked {news.asked})"
    assert search.calls == 1, "one macro read for the entire scan"
    assert llm.calls <= 6, "the research layer must not add a single model call"


@pytest.mark.asyncio
async def test_a_second_scan_the_same_day_costs_nothing_extra(seeded_session):
    news, search = CountingNews(), StubSearch()
    svc = DeployService(
        seeded_session,
        RisingAll(),
        CountingLLM(),
        MarketContextService(seeded_session, news, search),
    )
    user_id = await _user(seeded_session)

    await svc.plan(user_id, DeployPlanIn(amount=5_000, max_candidates=6))
    first_news, first_search = news.calls, search.calls

    await svc.plan(user_id, DeployPlanIn(amount=5_000, max_candidates=6))

    assert news.calls == first_news, "the day's snapshots must be reused"
    assert search.calls == first_search, "the macro brief is fetched once a day"


@pytest.mark.asyncio
async def test_news_is_never_fetched_for_names_the_scan_discarded(seeded_session):
    """Fetching during ranking instead of after quoting is the tempting bug."""
    news = CountingNews()
    svc = DeployService(
        seeded_session,
        RisingAll(),
        CountingLLM(),
        MarketContextService(seeded_session, news, StubSearch()),
    )
    user_id = await _user(seeded_session)

    plan = await svc.plan(user_id, DeployPlanIn(amount=5_000, max_candidates=3))

    picked = {p.symbol for p in plan.picks}
    assert len(news.asked) <= 3
    assert picked <= set(news.asked) or not picked


@pytest.mark.asyncio
async def test_research_disabled_costs_nothing_and_still_plans(seeded_session, monkeypatch):
    monkeypatch.setattr(app_settings, "research_enabled", False)
    news, search = CountingNews(), StubSearch()
    svc = DeployService(
        seeded_session,
        RisingAll(),
        CountingLLM(),
        MarketContextService(seeded_session, news, search),
    )
    user_id = await _user(seeded_session)

    plan = await svc.plan(user_id, DeployPlanIn(amount=5_000, max_candidates=6))

    assert news.calls == 0 and search.calls == 0
    assert plan.considered >= 60, "the plan itself must be unaffected"
