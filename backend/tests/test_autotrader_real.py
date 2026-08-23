"""The auto-trader against the real portfolio.

It used to trade the paper sandbox. It now reads real holdings and writes
PROPOSED orders, which puts it under the same rule as every other engine in this
module: it may recommend, and only a human may approve. These tests exist mostly
to pin that down — an engine that could set APPROVED, or that sold something the
user does not hold, would be a much worse bug than a bad signal.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.models import ProposedOrder
from app.domain.enums import ProposedOrderStatus
from app.integrations.marketdata.provider import BarPoint, MarketDataError, SymbolQuote
from app.repositories.holdings import HoldingRepository
from app.repositories.user_settings import UserSettingsRepository
from app.repositories.users import UserRepository
from app.schemas.paper import AutoTraderConfigIn
from app.services.autotrader_service import MIN_PROPOSAL_CAD, AutoTraderService


# A straight line is the wrong shape to test with: it drives RSI to 100, which
# the buy rule correctly refuses as overbought. Real uptrends pull back, so the
# series below rises through a gentle oscillation — trend up, RSI mid-40s, 3m
# return positive, which is what the rules are actually written for.
def _series(base: float, slope: float, n: int = 200) -> list[float]:
    return [base + i * slope + 6 * math.sin(i / 4.0) for i in range(n)]


class RisingProvider:
    """An uptrend with normal pullbacks — candidates are buy signals."""

    async def get_quote(self, symbol: str) -> SymbolQuote:
        if symbol.upper() == "BADX":
            raise MarketDataError("unknown")
        currency = "CAD" if symbol.upper().endswith(".TO") else "USD"
        return SymbolQuote(symbol.upper(), 100.0, currency, f"{symbol.upper()} Inc")

    async def get_bars(self, symbol: str, start: date, end: date) -> list[BarPoint]:
        if symbol.upper() == "USDCAD=X":
            return [BarPoint(start + timedelta(days=i), 1.4) for i in range(200)]
        return [BarPoint(start + timedelta(days=i), c) for i, c in enumerate(_series(100.0, 0.5))]


class FallingProvider:
    """A downtrend — anything held is an exit signal."""

    async def get_quote(self, symbol: str) -> SymbolQuote:
        currency = "CAD" if symbol.upper().endswith(".TO") else "USD"
        return SymbolQuote(symbol.upper(), 100.0, currency, f"{symbol.upper()} Inc")

    async def get_bars(self, symbol: str, start: date, end: date) -> list[BarPoint]:
        if symbol.upper() == "USDCAD=X":
            return [BarPoint(start + timedelta(days=i), 1.4) for i in range(200)]
        return [BarPoint(start + timedelta(days=i), c) for i, c in enumerate(_series(300.0, -0.5))]


async def _user(session) -> uuid.UUID:
    u = await UserRepository(session).create(
        email=f"bot{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    return u.id


async def _hold(session, user_id, symbol: str, qty: float) -> None:
    await HoldingRepository(session).upsert(
        user_id=user_id,
        symbol=symbol,
        qty=qty,
        avg_cost_cents=10_000,
        currency="CAD" if symbol.endswith(".TO") else "USD",
        name=f"{symbol} Inc",
        is_synced=False,
    )
    await session.flush()


async def _cash(session, user_id, dollars: float) -> None:
    row = await UserSettingsRepository(session).get_or_create(user_id)
    row.invest_cash_cents = round(dollars * 100)
    await session.flush()


async def _arm(svc: AutoTraderService, user_id, **kwargs) -> None:
    await svc.update_config(user_id, AutoTraderConfigIn(enabled=True, dry_run=False, **kwargs))


async def _orders(session, user_id) -> list[ProposedOrder]:
    result = await session.execute(
        select(ProposedOrder).where(
            ProposedOrder.user_id == user_id,
            ProposedOrder.status == ProposedOrderStatus.PROPOSED,
        )
    )
    return list(result.scalars().all())


# --- the rule that matters -------------------------------------------------
@pytest.mark.asyncio
async def test_the_bot_can_only_ever_propose(seeded_session):
    """No engine path may produce anything but PROPOSED."""
    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 5_000)
    svc = AutoTraderService(seeded_session, RisingProvider())
    await _arm(svc, user_id, universe=["VFV.TO"], per_trade=500)

    await svc.run(user_id)

    orders = await _orders(seeded_session, user_id)
    assert orders
    assert all(o.status == ProposedOrderStatus.PROPOSED for o in orders)
    assert all(o.kind == "autotrader" for o in orders)


@pytest.mark.asyncio
async def test_a_dry_run_writes_nothing(seeded_session):
    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 5_000)
    svc = AutoTraderService(seeded_session, RisingProvider())
    await svc.update_config(
        user_id, AutoTraderConfigIn(enabled=True, dry_run=True, universe=["VFV.TO"])
    )

    run = await svc.run(user_id)

    assert run.executed == 0
    assert await _orders(seeded_session, user_id) == []
    assert "report only" in run.summary


# --- selling ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_it_only_sells_what_you_actually_hold(seeded_session):
    """A sell signal on something you don't own is not a short — it's nothing."""
    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 0)
    svc = AutoTraderService(seeded_session, FallingProvider())
    await _arm(svc, user_id, universe=["VFV.TO", "XEQT.TO"], per_trade=500)

    await svc.run(user_id)

    assert await _orders(seeded_session, user_id) == []


@pytest.mark.asyncio
async def test_a_held_position_in_a_downtrend_is_proposed_for_exit(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "VFV.TO", 10)
    await _cash(seeded_session, user_id, 0)
    svc = AutoTraderService(seeded_session, FallingProvider())
    await _arm(svc, user_id, universe=["VFV.TO"], per_trade=500)

    await svc.run(user_id)

    (order,) = await _orders(seeded_session, user_id)
    assert order.symbol == "VFV.TO"
    assert order.side.value == "sell"
    assert order.notional_cents > 0


@pytest.mark.asyncio
async def test_holdings_are_scanned_even_when_not_in_the_universe(seeded_session):
    """A scanner blind to what you own can't tell you something has turned."""
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "ZAG.TO", 10)
    await _cash(seeded_session, user_id, 0)
    svc = AutoTraderService(seeded_session, FallingProvider())
    await _arm(svc, user_id, universe=["VFV.TO"], per_trade=500)

    run = await svc.run(user_id)

    assert "ZAG.TO" in [d.symbol for d in run.decisions]


# --- buying ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_buys_never_exceed_available_cash(seeded_session):
    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 300)
    svc = AutoTraderService(seeded_session, RisingProvider())
    await _arm(
        svc, user_id, universe=["VFV.TO", "XEQT.TO", "ZAG.TO"], per_trade=500, max_positions=10
    )

    await svc.run(user_id)

    orders = await _orders(seeded_session, user_id)
    spent = sum(o.notional_cents for o in orders) / 100
    assert spent <= 300 + 0.01
    assert all(o.notional_cents / 100 >= MIN_PROPOSAL_CAD for o in orders)


@pytest.mark.asyncio
async def test_max_positions_bounds_new_buys(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "SPY", 10)
    await _cash(seeded_session, user_id, 10_000)
    svc = AutoTraderService(seeded_session, RisingProvider())
    await _arm(
        svc, user_id, universe=["VFV.TO", "XEQT.TO", "ZAG.TO"], per_trade=500, max_positions=2
    )

    await svc.run(user_id)

    buys = [o for o in await _orders(seeded_session, user_id) if o.side.value == "buy"]
    assert len(buys) == 1  # one existing holding + one new = the cap


@pytest.mark.asyncio
async def test_the_paper_account_is_left_alone(seeded_session):
    """The bot moved off the sandbox — it must not touch it any more."""
    from app.services.paper_broker_service import PaperBrokerService

    user_id = await _user(seeded_session)
    await _cash(seeded_session, user_id, 5_000)
    svc = AutoTraderService(seeded_session, RisingProvider())
    await _arm(svc, user_id, universe=["VFV.TO"], per_trade=500)

    await svc.run(user_id)

    paper = await PaperBrokerService(seeded_session, RisingProvider()).portfolio(user_id)
    assert paper.positions == []
