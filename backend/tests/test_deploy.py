"""Cash deployment: "I have $X to invest" -> a whole-share buy plan.

Covers the two things that make this engine safe to ship: sizing can never
overspend the amount, and accepting a plan can only ever produce PROPOSED
orders (no path anywhere sets APPROVED).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient

from app.core.config import settings as app_settings
from app.domain.enums import ProposedOrderStatus
from app.integrations.marketdata.provider import BarPoint, MarketDataError, SymbolQuote
from app.repositories.users import UserRepository
from app.schemas.invest import DeployAcceptIn, DeployOrderIn, DeployPlanIn
from app.services.deploy_service import DeployService

PREFIX = "/api/v1"


class FakeProvider:
    """Everything prices at 100 on a rising 60-day series; USDCAD is 1.4."""

    async def get_quote(self, symbol: str) -> SymbolQuote:
        if symbol.upper() == "BADX":
            raise MarketDataError("unknown")
        currency = "CAD" if symbol.upper().endswith(".TO") else "USD"
        return SymbolQuote(symbol.upper(), 100.0, currency, f"{symbol.upper()} Inc")

    async def get_bars(self, symbol: str, start: date, end: date) -> list[BarPoint]:
        if symbol.upper() == "USDCAD=X":
            return [BarPoint(start + timedelta(days=i), 1.4) for i in range(60)]
        return [BarPoint(start + timedelta(days=i), 100.0 + i) for i in range(60)]


class BuyLLM:
    """Every agent bullish, CIO returns BUY — exercises the sizing path."""

    model = "fake"

    async def complete(self, prompt: str, *, system=None, temperature: float = 0.0) -> str:
        if '"action"' in prompt:  # the CIO's synthesis prompt
            return '{"action":"BUY","confidence":0.8,"rationale":"Strong uptrend."}'
        return '{"stance":"bullish","confidence":0.7,"thesis":"Momentum is favourable."}'

    async def is_available(self) -> bool:
        return True


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


async def _register(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post(
        f"{PREFIX}/auth/register", json={"email": email, "password": "supersecret123"}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# --- sizing ---
@pytest.mark.asyncio
async def test_plan_buys_whole_shares_within_budget(seeded_session, monkeypatch):
    monkeypatch.setattr(app_settings, "ai_enabled", True)
    user_id = await _user(seeded_session)
    svc = DeployService(seeded_session, FakeProvider(), BuyLLM())

    plan = await svc.plan(user_id, DeployPlanIn(amount=5000))

    assert plan.source == "ai"
    assert plan.picks, "a bullish committee should produce buys"
    for pick in plan.picks:
        assert isinstance(pick.qty, int) and pick.qty >= 1  # never fractional
        assert pick.est_value == pytest.approx(pick.qty * pick.price, abs=0.01)
    # The whole point: rounding down can never overspend the amount.
    assert plan.invested <= 5000
    assert plan.cash_remaining == pytest.approx(5000 - plan.invested, abs=0.01)
    assert plan.cash_remaining >= 0


@pytest.mark.asyncio
async def test_share_costing_more_than_its_slice_is_dropped(seeded_session, monkeypatch):
    monkeypatch.setattr(app_settings, "ai_enabled", True)
    user_id = await _user(seeded_session)
    svc = DeployService(seeded_session, FakeProvider(), BuyLLM())

    # $100 split across candidates leaves each a slice below one $140 share.
    plan = await svc.plan(user_id, DeployPlanIn(amount=100))

    assert plan.picks == []
    assert plan.invested == 0
    assert plan.cash_remaining == 100
    assert any(p.action == "too-expensive" for p in plan.passed)


# --- degradation ---
@pytest.mark.asyncio
async def test_plan_still_works_with_the_llm_down(seeded_session):
    user_id = await _user(seeded_session)
    svc = DeployService(seeded_session, FakeProvider(), UnavailableLLM())

    plan = await svc.plan(user_id, DeployPlanIn(amount=5000))

    assert plan.source == "rules"  # degraded, not failed
    assert plan.considered > 0
    assert plan.invested <= 5000


@pytest.mark.asyncio
async def test_unpriceable_candidates_are_skipped_not_fatal(client: AsyncClient, monkeypatch):
    """NVDA is in DEFAULT_UNIVERSE but not in the mock provider's curves."""
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)
    monkeypatch.setattr(app_settings, "ai_enabled", False)  # keep it fast — no LLM
    headers = await _register(client, "deploy-unpriced@test.com")

    resp = await client.post(
        f"{PREFIX}/invest/deploy/propose", headers=headers, json={"amount": 5000}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["considered"] > 0
    assert body["invested"] <= 5000
    assert all(float(p["qty"]).is_integer() for p in body["picks"])
    assert any(p["symbol"] == "NVDA" for p in body["passed"])


# --- accepting ---
@pytest.mark.asyncio
async def test_accept_creates_proposed_orders_only(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)
    headers = await _register(client, "deploy-accept@test.com")

    resp = await client.post(
        f"{PREFIX}/invest/deploy/accept",
        headers=headers,
        json={
            "orders": [
                {"symbol": "VFV.TO", "qty": 12, "price": 100.0, "reason": "committee BUY"},
                {"symbol": "XEQT.TO", "qty": 30, "price": 20.0, "reason": ""},
            ]
        },
    )

    assert resp.status_code == 200, resp.text
    proposal = resp.json()
    assert proposal["total_buy"] == pytest.approx(12 * 100.0 + 30 * 20.0, abs=0.01)
    assert proposal["total_sell"] == 0
    assert proposal["disclaimer"]

    listed = await client.get(f"{PREFIX}/invest/orders/proposed?status=proposed", headers=headers)
    orders = listed.json()
    assert {o["symbol"] for o in orders} == {"VFV.TO", "XEQT.TO"}
    for o in orders:
        assert o["kind"] == "deploy"
        assert o["side"] == "buy"
        # The whole safety model: an engine may only ever propose.
        assert o["status"] == ProposedOrderStatus.PROPOSED.value
        assert o["reason"]  # every order explains itself


@pytest.mark.asyncio
async def test_accept_supersedes_an_earlier_open_batch(seeded_session):
    user_id = await _user(seeded_session)
    svc = DeployService(seeded_session, FakeProvider(), UnavailableLLM())
    order = DeployOrderIn(symbol="VFV.TO", qty=5, price=100.0)

    first = await svc.accept(user_id, DeployAcceptIn(orders=[order]))
    second = await svc.accept(user_id, DeployAcceptIn(orders=[order]))

    assert first.batch_id != second.batch_id
    open_orders = await svc.proposals.list_for_user(user_id, ProposedOrderStatus.PROPOSED)
    assert len(open_orders) == 1  # the stale checklist was expired
    assert str(open_orders[0].batch_id) == second.batch_id
