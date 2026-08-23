"""AI committee (rules fallback), paper broker, and auto-trader safety."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.integrations.marketdata.provider import BarPoint, MarketDataError, SymbolQuote
from app.repositories.users import UserRepository
from app.schemas.paper import AutoTraderConfigIn
from app.services.autotrader_service import AutoTraderService
from app.services.committee_service import CommitteeService
from app.services.errors import ValidationFailed
from app.services.paper_broker_service import PaperBrokerService


class FakeProvider:
    """Prices anything but BADX; serves a rising 60-day series (offline)."""

    async def get_quote(self, symbol: str) -> SymbolQuote:
        if symbol.upper() == "BADX":
            raise MarketDataError("unknown")
        currency = "CAD" if symbol.upper().endswith(".TO") else "USD"
        return SymbolQuote(symbol.upper(), 100.0, currency, f"{symbol.upper()} Inc")

    async def get_bars(self, symbol: str, start: date, end: date) -> list[BarPoint]:
        if symbol.upper() == "USDCAD=X":
            return [BarPoint(start + timedelta(days=i), 1.4) for i in range(60)]
        return [BarPoint(start + timedelta(days=i), 100.0 + i) for i in range(60)]


class UnavailableLLM:
    async def complete(self, *a, **k):
        raise RuntimeError("offline")

    async def is_available(self) -> bool:
        return False


async def _user(session) -> uuid.UUID:
    u = await UserRepository(session).create(
        email=f"u{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    return u.id


@pytest.mark.asyncio
async def test_committee_rules_fallback(seeded_session):
    user_id = await _user(seeded_session)
    svc = CommitteeService(seeded_session, FakeProvider(), UnavailableLLM())

    res = await svc.analyze(user_id, "aapl")
    assert res.source == "rules"  # LLM offline -> transparent rules verdict
    assert res.action in {"BUY", "SKIP", "HOLD"}
    assert len(res.agents) == 5
    assert res.indicators["trend"] == "up"

    sessions = await svc.list_sessions(user_id)
    assert len(sessions) == 1
    assert sessions[0].symbol == "AAPL"

    with pytest.raises(ValidationFailed):
        await svc.analyze(user_id, "BADX")


@pytest.mark.asyncio
async def test_paper_buy_sell_and_cash(seeded_session):
    user_id = await _user(seeded_session)
    svc = PaperBrokerService(seeded_session, FakeProvider())

    await svc.buy(user_id, "TD.TO", 10)  # 10 * 100 CAD = 1000
    pf = await svc.portfolio(user_id)
    assert pf.account.cash == 100_000.0 - 1_000.0
    assert len(pf.positions) == 1 and pf.positions[0].qty == 10

    await svc.sell(user_id, "TD.TO", 4)
    pf = await svc.portfolio(user_id)
    assert pf.positions[0].qty == 6

    with pytest.raises(ValidationFailed):  # can't sell more than held
        await svc.sell(user_id, "TD.TO", 999)
    with pytest.raises(ValidationFailed):  # can't overspend paper cash
        await svc.buy(user_id, "TD.TO", 100_000)


@pytest.mark.asyncio
async def test_autotrader_is_safe_by_default(seeded_session):
    user_id = await _user(seeded_session)
    svc = AutoTraderService(seeded_session, FakeProvider())

    cfg = await svc.get_config(user_id)
    assert cfg.enabled is False and cfg.dry_run is True  # never trades until opted in

    run = await svc.run(user_id)
    assert run.scanned == len(cfg.universe)
    assert run.executed == 0
    assert all(d.executed is False for d in run.decisions)

    # Enabled-but-dry-run reports without writing to the trade checklist.
    await svc.update_config(user_id, AutoTraderConfigIn(enabled=True, dry_run=True))
    run2 = await svc.run(user_id)
    assert run2.executed == 0
    # The bot works on the real portfolio now and must leave the sandbox alone.
    paper = await PaperBrokerService(seeded_session, FakeProvider()).portfolio(user_id)
    assert paper.positions == []
