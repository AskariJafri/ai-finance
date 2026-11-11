"""Prices from the user's own brokerage first, market data otherwise."""

from __future__ import annotations

import pytest

from app.core.crypto import get_cipher
from app.db.models import BrokerageConnection
from app.integrations.snaptrade.gateway import SnapTradeError, SnapTradeQuote
from app.integrations.snaptrade.mock_client import MockSnapTradeClient
from app.repositories.user_settings import UserSettingsRepository
from app.schemas.invest import ActionPlanIn
from app.services.live_quotes import broker_quote_fetcher
from app.services.portfolio_actions_service import PortfolioActionsService
from tests.test_portfolio_actions import ScriptedLLM, SignalProvider, _cash, _hold, _user


async def _link(session, user_id, institution="Wealthsimple"):
    row = await UserSettingsRepository(session).get_or_create(user_id)
    row.snaptrade_user_id = "snap-user"
    row.snaptrade_secret_encrypted = get_cipher().encrypt("secret")
    session.add(
        BrokerageConnection(
            user_id=user_id, brokerage_account_id="acct-1", institution_name=institution
        )
    )
    await session.commit()


class CountingBroker(MockSnapTradeClient):
    def __init__(self, prices: dict[str, float], fail: bool = False):
        super().__init__()
        self.prices = prices
        self.fail = fail
        self.calls: list[list[str]] = []

    async def get_quotes(self, *, tickers, **kwargs):
        self.calls.append(list(tickers))
        if self.fail:
            raise SnapTradeError("quotes endpoint disabled on this plan")
        return [SnapTradeQuote(t, self.prices[t]) for t in tickers if t in self.prices]


@pytest.mark.asyncio
async def test_no_link_means_no_broker_quotes(seeded_session):
    user_id = await _user(seeded_session)
    fetch = await broker_quote_fetcher(seeded_session, user_id, CountingBroker({}), get_cipher())
    assert fetch is None


@pytest.mark.asyncio
async def test_broker_quotes_respect_the_ten_symbol_cap(seeded_session):
    user_id = await _user(seeded_session)
    await _link(seeded_session, user_id)
    tickers = [f"T{i}" for i in range(23)]
    broker = CountingBroker({t: 10.0 for t in tickers})
    fetch = await broker_quote_fetcher(seeded_session, user_id, broker, get_cipher())
    quotes = await fetch(tickers)
    assert len(quotes) == 23
    assert [len(c) for c in broker.calls] == [10, 10, 3]
    q = quotes["T0"]
    assert q.source == "broker:Wealthsimple" and q.as_of is not None
    assert q.currency == "USD"


@pytest.mark.asyncio
async def test_a_plan_buys_at_the_broker_price_and_says_so(seeded_session):
    user_id = await _user(seeded_session)
    await _link(seeded_session, user_id)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 5_000)

    broker = CountingBroker({"XEQT.TO": 31.80, "SPY": 600.0, "VEQT.TO": 45.0})
    svc = PortfolioActionsService(
        seeded_session,
        SignalProvider(),
        ScriptedLLM({}, default=("BUY", 0.8)),
        broker=broker,
        cipher=get_cipher(),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    buys = [a for a in plan.actions if a.side == "buy"]
    assert buys
    for a in buys:
        assert a.price_as_of
        if a.symbol in broker.prices:
            assert a.price_source == "broker:Wealthsimple"
        else:
            assert a.price_source == "yahoo" or a.price_source is None
    # One call per stage at most — never a poll.
    assert len(broker.calls) <= 2


@pytest.mark.asyncio
async def test_a_broker_outage_falls_back_to_market_data(seeded_session):
    user_id = await _user(seeded_session)
    await _link(seeded_session, user_id)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 5_000)

    svc = PortfolioActionsService(
        seeded_session,
        SignalProvider(),
        ScriptedLLM({}, default=("BUY", 0.8)),
        broker=CountingBroker({}, fail=True),
        cipher=get_cipher(),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    assert [a for a in plan.actions if a.side == "buy"], "an outage must not empty the plan"
    assert all(not (a.price_source or "").startswith("broker") for a in plan.actions)
