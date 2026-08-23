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

from app.ai.committee import ROLES
from app.core.config import settings as app_settings
from app.domain.enums import ProposedOrderStatus
from app.integrations.marketdata.mock_client import CURVES
from app.integrations.marketdata.provider import BarPoint, MarketDataError, SymbolQuote
from app.repositories.users import UserRepository
from app.schemas.invest import DeployAcceptIn, DeployOrderIn, DeployPlanIn
from app.services.deploy_service import (
    CONCENTRATION_CAP,
    MAX_PASSED_REPORTED,
    DeployService,
)

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
    """Every seat bullish and the CIO returns BUY — exercises the sizing path.

    One reply per ticker, since the whole committee is a single call.
    """

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, prompt: str, *, system=None, temperature: float = 0.0, json_object: bool = False
    ) -> str:
        self.calls += 1
        agents = ", ".join(
            f'{{"agent":"{key}","stance":"bullish","confidence":0.7,'
            f'"thesis":"Momentum is favourable."}}'
            for key, _, _ in ROLES
        )
        return (
            f'{{"agents":[{agents}],"action":"BUY","confidence":0.8,"rationale":"Strong uptrend."}}'
        )

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
    assert plan.sizing == "shares"
    for pick in plan.picks:
        # Whole shares: the quantity is carried as a float so the same field can
        # hold a fractional dollar-sized quantity, but under share sizing it
        # must always land on a whole number a broker could actually be handed.
        assert pick.qty == int(pick.qty) and pick.qty >= 1
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

    # $50 is below one share of anything quoted (100 CAD, or 140 after FX).
    plan = await svc.plan(user_id, DeployPlanIn(amount=50))

    assert plan.picks == []
    assert plan.invested == 0
    assert plan.cash_remaining == 50
    assert any(p.action == "too-expensive" for p in plan.passed)


# --- dollar sizing ---
@pytest.mark.asyncio
async def test_dollar_sizing_spends_the_whole_amount(seeded_session, monkeypatch):
    """The point of dollar sizing: no leftover cash and no dropped names.

    Whole-share sizing has to round down, so a name whose share price exceeds
    its slice falls out of the plan entirely. Sized in dollars every winner gets
    funded, which is what a fractional-share broker will actually take.
    """
    monkeypatch.setattr(app_settings, "ai_enabled", True)
    user_id = await _user(seeded_session)
    svc = DeployService(seeded_session, FakeProvider(), BuyLLM())

    plan = await svc.plan(user_id, DeployPlanIn(amount=5000, sizing="dollars"))

    assert plan.sizing == "dollars"
    assert plan.picks
    # Exactly the amount, never a cent over: rounding drift is absorbed into
    # the largest position rather than left to overshoot the budget.
    assert plan.invested == pytest.approx(5000, abs=0.01)
    assert plan.cash_remaining == pytest.approx(0, abs=0.01)
    assert plan.cash_remaining >= 0
    for pick in plan.picks:
        assert pick.qty > 0
        assert pick.est_value == pytest.approx(pick.qty * pick.price, abs=0.05)


@pytest.mark.asyncio
async def test_dollar_sizing_works_below_one_share(seeded_session, monkeypatch):
    """An amount too small to buy a single share still produces a plan.

    Under share sizing this same amount returns nothing at all — which is the
    case a $478 account hits against a $763 share.
    """
    monkeypatch.setattr(app_settings, "ai_enabled", True)
    user_id = await _user(seeded_session)
    svc = DeployService(seeded_session, FakeProvider(), BuyLLM())

    shares = await svc.plan(user_id, DeployPlanIn(amount=50))
    dollars = await svc.plan(user_id, DeployPlanIn(amount=50, sizing="dollars"))

    assert shares.picks == []
    assert dollars.picks
    assert all(p.qty < 1 for p in dollars.picks)
    assert dollars.invested == pytest.approx(50, abs=1.0)


@pytest.mark.asyncio
async def test_dollar_sizing_still_respects_the_concentration_cap(seeded_session, monkeypatch):
    monkeypatch.setattr(app_settings, "ai_enabled", True)
    user_id = await _user(seeded_session)
    svc = DeployService(seeded_session, FakeProvider(), BuyLLM())

    plan = await svc.plan(user_id, DeployPlanIn(amount=5000, sizing="dollars"))

    for pick in plan.picks:
        assert pick.weight <= CONCENTRATION_CAP + 1e-6


@pytest.mark.asyncio
async def test_accepting_a_dollar_plan_writes_notional_orders(seeded_session):
    """Dollar orders carry an amount and no quantity.

    ``BrokerageService.approve_and_submit`` refuses a notional order, so this
    also pins the boundary: dollar-sized plans stay advisory.
    """
    user_id = await _user(seeded_session)
    svc = DeployService(seeded_session, FakeProvider(), BuyLLM())

    proposal = await svc.accept(
        user_id,
        DeployAcceptIn(
            orders=[DeployOrderIn(symbol="XEQT.TO", qty=1.5, price=100.0)],
            sizing="dollars",
        ),
    )

    (order,) = proposal.orders
    assert order.qty is None
    assert order.notional == pytest.approx(150.0)
    assert order.est_value == pytest.approx(150.0)
    assert "$150.00" in order.reason


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
    """Most of DEPLOY_UNIVERSE is absent from the mock provider's curves.

    The scan has to survive that: an unknown ticker costs us the candidate, not
    the request.
    """
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)
    monkeypatch.setattr(app_settings, "ai_enabled", False)  # keep it fast — no LLM
    headers = await _register(client, "deploy-unpriced@test.com")

    resp = await client.post(
        f"{PREFIX}/invest/deploy/propose", headers=headers, json={"amount": 5000}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The whole universe was scanned, not just the handful that price.
    assert body["considered"] > len(CURVES)
    assert body["invested"] <= 5000
    assert all(float(p["qty"]).is_integer() for p in body["picks"])
    assert any(p["action"] == "unpriced" for p in body["passed"])
    # ...but the user is not shown one line per unknown ticker.
    assert len(body["passed"]) <= MAX_PASSED_REPORTED


# --- allocation ---
def test_allocate_sweeps_leftover_cash_into_what_is_affordable():
    """The floor-and-drop allocator this replaced bought nothing here.

    $250 slices, a $600 share and a $30 share: proportional-then-drop left the
    entire $500 in cash.
    """
    qty = DeployService._allocate(
        500.0, {"SPY": 0.5, "XEQT.TO": 0.5}, {"SPY": 600.0, "XEQT.TO": 30.0}
    )

    assert qty["SPY"] == 0  # one share costs more than the whole amount
    spent = qty["SPY"] * 600.0 + qty["XEQT.TO"] * 30.0
    assert spent <= 500.0
    # SPY's weight was renormalised onto XEQT rather than stranded as cash.
    assert 500.0 - spent < 30.0


def test_allocate_never_overspends_and_splits_across_names():
    targets = {"A": 0.4, "B": 0.35, "C": 0.25}
    prices = {"A": 55.0, "B": 120.0, "C": 20.0}

    qty = DeployService._allocate(2000.0, targets, prices)

    spent = sum(qty[s] * prices[s] for s in targets)
    assert spent <= 2000.0
    assert all(isinstance(q, int) for q in qty.values())
    assert sum(1 for q in qty.values() if q > 0) == 3  # genuinely split
    assert 2000.0 - spent < min(prices.values())  # nothing affordable stranded


def test_allocate_caps_concentration_when_others_can_participate():
    """A cheap name must not swallow a plan the others could have shared."""
    targets = {"CHEAP": 0.34, "MID": 0.33, "ALSO": 0.33}
    prices = {"CHEAP": 5.0, "MID": 90.0, "ALSO": 80.0}

    qty = DeployService._allocate(1000.0, targets, prices)

    cheap_value = qty["CHEAP"] * 5.0
    assert cheap_value <= CONCENTRATION_CAP * 1000.0
    assert qty["MID"] >= 1 and qty["ALSO"] >= 1


def test_allocate_seeds_a_dear_name_before_a_cheap_one_eats_the_budget():
    """Allocating strictly in proportion gives the $10 name its $500 slice
    first, leaving $400 against a $600 share that can then never be bought."""
    targets = {"CHEAP": 0.5, "PRICEY": 0.5}
    prices = {"CHEAP": 10.0, "PRICEY": 600.0}

    qty = DeployService._allocate(1000.0, targets, prices)

    assert qty["PRICEY"] == 1
    assert qty["CHEAP"] > 0
    assert sum(qty[s] * prices[s] for s in targets) <= 1000.0


def test_allocate_will_not_seed_a_name_that_is_dear_for_its_conviction():
    """The seed is a tolerance, not a free pass: 5% conviction doesn't buy a
    $600 share out of $1,000."""
    targets = {"CHEAP": 0.95, "PRICEY": 0.05}
    prices = {"CHEAP": 10.0, "PRICEY": 600.0}

    qty = DeployService._allocate(1000.0, targets, prices)

    assert qty["PRICEY"] == 0
    assert qty["CHEAP"] * 10.0 >= 900.0


@pytest.mark.asyncio
async def test_max_positions_limits_how_many_names_the_amount_is_split_across(
    seeded_session, monkeypatch
):
    monkeypatch.setattr(app_settings, "ai_enabled", True)
    user_id = await _user(seeded_session)
    svc = DeployService(seeded_session, FakeProvider(), BuyLLM())

    plan = await svc.plan(user_id, DeployPlanIn(amount=10_000, max_candidates=6, max_positions=2))

    assert 1 <= len(plan.picks) <= 2
    assert plan.invested <= 10_000


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
