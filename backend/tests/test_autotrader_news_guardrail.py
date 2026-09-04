"""The news guardrail on the auto-trader.

This is the only place in the app where third-party text changes a decision
with no human and no model in the loop, so the invariants matter more than
usual: it may only ever downgrade, it only looks at symbols it could actually
act on, and it fails open when the research layer is unavailable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings as app_settings
from app.integrations.news.provider import NewsArticle, NewsError
from app.services.autotrader_service import AutoTraderService, apply_news_guardrail
from app.services.market_context_service import MarketContextService
from tests.test_autotrader_real import (
    RisingProvider,
    _arm,
    _cash,
    _orders,
    _user,
)
from tests.test_market_context_service import StubSearch


class CountingNews:
    """Records exactly which symbols were asked about."""

    def __init__(self, script: dict[str, list[str]] | None = None, explode: bool = False):
        self.script = script or {}
        self.explode = explode
        self.calls = 0
        self.asked: list[str] = []

    async def company_news(self, symbol, start, end, *, limit=8):
        self.calls += 1
        self.asked.append(symbol)
        if self.explode:
            raise NewsError("provider down")
        return [
            NewsArticle(
                published_at=datetime.now(UTC) - timedelta(hours=2),
                headline=h,
                summary="",
                source="Reuters",
                url="https://example.test/x",
                article_id=f"{symbol}-{i}",
            )
            for i, h in enumerate(self.script.get(symbol, []))
        ]

    async def is_available(self):
        return True


def _svc(session, news) -> AutoTraderService:
    return AutoTraderService(
        session, RisingProvider(), MarketContextService(session, news, StubSearch())
    )


@pytest.fixture(autouse=True)
def _research_on(monkeypatch):
    monkeypatch.setattr(app_settings, "research_enabled", True)
    monkeypatch.setattr(app_settings, "market_data_use_mock", False)


# --- the pure function ------------------------------------------------------


def test_the_guardrail_only_ever_downgrades():
    """Across every action, news must never invent a trade."""
    from datetime import date

    from app.analytics.decision_context import EventFlag

    flag = EventFlag("guidance_cut", -1, 3, "Acme cuts guidance", "Reuters", date.today())
    for action in ("hold", "skip", "sell"):
        out_action, _reason, moved = apply_news_guardrail(action, "r", flag)
        assert out_action == action, f"{action} must be left alone"
        assert moved is False


def test_a_buy_with_no_flag_is_untouched():
    action, reason, moved = apply_news_guardrail("buy", "uptrend", None)
    assert (action, reason, moved) == ("buy", "uptrend", False)


def test_a_downgraded_buy_explains_itself():
    from datetime import date

    from app.analytics.decision_context import EventFlag

    flag = EventFlag("guidance_cut", -1, 3, "Acme cuts guidance", "Reuters", date(2026, 8, 22))
    action, reason, moved = apply_news_guardrail("buy", "uptrend, RSI 55", flag)

    assert action == "skip" and moved is True
    assert "guidance_cut" in reason
    assert "2026-08-22" in reason, "the user needs the date to judge staleness"
    assert "the rules said buy" in reason, "say what was overridden, not just that it was"


# --- end to end -------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_bad_news_stops_a_buy_reaching_the_checklist(seeded_session):
    news = CountingNews({"AAPL": ["Acme cuts full-year revenue guidance"]})
    svc = _svc(seeded_session, news)
    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 5_000)
    await _arm(svc, user_id, universe=["AAPL"], per_trade=500)

    run = await svc.run(user_id)

    decision = next(d for d in run.decisions if d.symbol == "AAPL")
    assert decision.action == "skip"
    assert await _orders(seeded_session, user_id) == []


@pytest.mark.asyncio
async def test_a_clean_tape_still_buys(seeded_session):
    """The guardrail must not become a blanket veto."""
    news = CountingNews({"AAPL": ["VFV.TO names new operations lead"]})
    svc = _svc(seeded_session, news)
    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 5_000)
    await _arm(svc, user_id, universe=["AAPL"], per_trade=500)

    await svc.run(user_id)

    assert await _orders(seeded_session, user_id)


@pytest.mark.asyncio
async def test_only_symbols_the_rules_would_buy_are_looked_up(seeded_session):
    """The cost guarantee for this surface.

    A hold, a skip and a sell cannot be changed by a downgrade-only guardrail,
    so asking about them would spend a metered call on an unusable answer.
    """
    news = CountingNews()
    svc = _svc(seeded_session, news)
    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 5_000)
    # Four names, all rising -> all buys, but the position cap is applied after
    # the guardrail, so the lookup set is the rules-buy set.
    await _arm(svc, user_id, universe=["AAPL", "MSFT", "NVDA", "GOOGL"], per_trade=500)

    await svc.run(user_id)

    assert news.calls == len(news.asked)
    assert set(news.asked) <= {"AAPL", "MSFT", "NVDA", "GOOGL"}
    assert news.calls <= 4


@pytest.mark.asyncio
async def test_a_news_outage_leaves_the_rules_verdict_untouched(seeded_session):
    """Fail open. A research outage must not stop the bot deciding."""
    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 5_000)

    broken = _svc(seeded_session, CountingNews(explode=True))
    await _arm(broken, user_id, universe=["AAPL"], per_trade=500)
    run = await broken.run(user_id)

    decision = next(d for d in run.decisions if d.symbol == "AAPL")
    assert decision.action == "buy", "an unreachable provider must not veto anything"
    assert await _orders(seeded_session, user_id)


@pytest.mark.asyncio
async def test_the_bot_works_with_no_research_layer_at_all(seeded_session):
    """context=None is the pre-feature behaviour, and must stay supported."""
    svc = AutoTraderService(seeded_session, RisingProvider())
    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 5_000)
    await _arm(svc, user_id, universe=["AAPL"], per_trade=500)

    run = await svc.run(user_id)

    assert next(d for d in run.decisions if d.symbol == "AAPL").action == "buy"


@pytest.mark.asyncio
async def test_a_vetoed_buy_does_not_consume_the_budget(seeded_session):
    """Why sizing runs after the guardrail, not with it.

    If the two passes were fused, a buy that news vetoes would still spend
    budget on its way to being discarded, quietly starving the next candidate.
    """
    news = CountingNews({"AAPL": ["Acme cuts full-year revenue guidance"]})
    svc = _svc(seeded_session, news)
    user_id = await _user(seeded_session)
    # Exactly one trade's worth of cash: if the vetoed name eats it, the second
    # name cannot be funded and the bug shows up as a missing order.
    await _cash(seeded_session, user_id, 500)
    await _arm(svc, user_id, universe=["AAPL", "MSFT"], per_trade=500)

    await svc.run(user_id)

    orders = await _orders(seeded_session, user_id)
    assert [o.symbol for o in orders] == ["MSFT"], "the freed budget must flow onward"
