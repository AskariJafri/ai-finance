"""The combined sell + buy planner.

The behaviour worth pinning down is not "does it produce orders" but the
guardrails around them: it must never sell more than is held, never spend more
than the sells plus cash actually raise, never propose a trade too small to be
worth placing, and never let a name it likes grow past the concentration cap.
Those are the properties that make a plan safe to act on.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.ai.committee import ROLES
from app.core.config import settings as app_settings
from app.integrations.marketdata.provider import BarPoint, MarketDataError, SymbolQuote
from app.repositories.holdings import HoldingRepository
from app.repositories.user_settings import UserSettingsRepository
from app.repositories.users import UserRepository
from app.schemas.invest import ActionAcceptIn, ActionOrderIn, ActionPlanIn
from app.services.deploy_service import CONCENTRATION_CAP
from app.services.portfolio_actions_service import (
    MIN_TRADE_CAD,
    PortfolioActionsService,
)

PREFIX = "/api/v1"

# The fake bar series runs 100..159 over 60 days, and a portfolio is valued at
# the latest close — so a share is worth $159, not the $100 the live quote says.
# Sells are deliberately priced off the position rather than the quote, so the
# expectations below use this.
CLOSE = 159.0


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


def _verdict(action: str, confidence: float) -> str:
    agents = ", ".join(
        f'{{"agent":"{key}","stance":"neutral","confidence":0.6,"thesis":"A view."}}'
        for key, _, _ in ROLES
    )
    return (
        f'{{"agents":[{agents}],"action":"{action}",'
        f'"confidence":{confidence},"rationale":"Reasoned verdict."}}'
    )


class ScriptedLLM:
    """Returns a verdict chosen by the ticker named in the prompt.

    The committee is one call per ticker, so keying off the prompt is enough to
    drive held positions and candidates down different branches in one run.
    Held positions are asked for TRIM/HOLD/ADD and candidates for BUY/SKIP/HOLD,
    so a verdict written in the wrong vocabulary is rejected and falls back to
    rules — which is itself worth not tripping over by accident.
    """

    model = "fake"

    def __init__(self, by_symbol: dict[str, tuple[str, float]], default=("SKIP", 0.5)):
        self.by_symbol = by_symbol
        self.default = default
        self.calls = 0

    async def complete(
        self, prompt: str, *, system=None, temperature: float = 0.0, json_object: bool = False
    ) -> str:
        self.calls += 1
        for symbol, (action, confidence) in self.by_symbol.items():
            if symbol in prompt:
                return _verdict(action, confidence)
        return _verdict(*self.default)

    async def is_available(self) -> bool:
        return True


async def _user(session) -> uuid.UUID:
    u = await UserRepository(session).create(
        email=f"u{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    return u.id


async def _hold(session, user_id, symbol: str, qty: float, cost_cents: int) -> None:
    await HoldingRepository(session).upsert(
        user_id=user_id,
        symbol=symbol,
        qty=qty,
        avg_cost_cents=cost_cents,
        currency="CAD" if symbol.endswith(".TO") else "USD",
        name=f"{symbol} Inc",
        is_synced=False,
    )
    await session.flush()


async def _cash(session, user_id, dollars: float) -> None:
    row = await UserSettingsRepository(session).get_or_create(user_id)
    row.invest_cash_cents = round(dollars * 100)
    await session.flush()


@pytest.fixture(autouse=True)
def _ai_on(monkeypatch):
    monkeypatch.setattr(app_settings, "ai_enabled", True)
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)


# --- selling ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_confident_trim_closes_the_position(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _hold(seeded_session, user_id, "ZAG.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(
        seeded_session,
        FakeProvider(),
        ScriptedLLM({"XEQT.TO": ("TRIM", 0.9), "ZAG.TO": ("HOLD", 0.6)}),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    sells = [a for a in plan.actions if a.side == "sell"]
    assert [(a.symbol, a.action) for a in sells] == [("XEQT.TO", "EXIT")]
    # The whole position, never more.
    assert sells[0].amount == pytest.approx(10 * CLOSE, abs=0.01)
    assert sells[0].projected_weight == 0.0
    assert [h.symbol for h in plan.holds] == ["ZAG.TO"]


@pytest.mark.asyncio
async def test_a_tentative_trim_only_halves_the_position(seeded_session):
    """An unsure committee is a reason to act on half the position, not all."""
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({"XEQT.TO": ("TRIM", 0.55)})
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    (sell,) = [a for a in plan.actions if a.side == "sell"]
    assert sell.action == "TRIM"
    assert sell.amount == pytest.approx(10 * CLOSE / 2, abs=0.01)
    assert sell.projected_weight == pytest.approx(0.5, abs=0.01)


@pytest.mark.asyncio
async def test_a_position_over_the_cap_is_trimmed_even_when_the_committee_likes_it(
    seeded_session,
):
    """Concentration is a portfolio fact, not a view on the company."""
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 90, 5_000)  # 90% of the account
    await _hold(seeded_session, user_id, "ZAG.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    # Both holdings are keepers; the shortlist supplies somewhere to redeploy.
    svc = PortfolioActionsService(
        seeded_session,
        FakeProvider(),
        ScriptedLLM({"XEQT.TO": ("HOLD", 0.8), "ZAG.TO": ("HOLD", 0.8)}, default=("BUY", 0.8)),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=2))

    (sell,) = [a for a in plan.actions if a.side == "sell"]
    assert sell.symbol == "XEQT.TO"
    assert sell.action == "TRIM"
    assert sell.projected_weight == pytest.approx(CONCENTRATION_CAP, abs=0.005)
    assert "single-position limit" in sell.reason


@pytest.mark.asyncio
async def test_a_lone_holding_is_not_trimmed_with_nowhere_to_redeploy(seeded_session):
    """One broad ETF is 100% concentrated and still a perfectly good portfolio.

    Selling it down to sit in cash is not diversification, so the cap only bites
    when the plan has somewhere else to put the money.
    """
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({}, default=("HOLD", 0.8))
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    assert plan.actions == []
    assert [h.symbol for h in plan.holds] == ["XEQT.TO"]


@pytest.mark.asyncio
async def test_a_sell_never_exceeds_what_is_held(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 3, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({"XEQT.TO": ("TRIM", 0.95)})
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    (sell,) = [a for a in plan.actions if a.side == "sell"]
    assert sell.amount <= 3 * CLOSE + 0.01


# --- buying ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_buys_are_funded_by_cash_plus_what_the_sells_raise(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)  # sold: $1000
    await _cash(seeded_session, user_id, 200)

    svc = PortfolioActionsService(
        seeded_session,
        FakeProvider(),
        ScriptedLLM({"XEQT.TO": ("TRIM", 0.9)}, default=("BUY", 0.8)),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))

    buys = [a for a in plan.actions if a.side == "buy"]
    sells = [a for a in plan.actions if a.side == "sell"]
    assert buys and sells
    budget = plan.cash_before + sum(a.amount for a in sells)
    assert sum(a.amount for a in buys) <= budget + 0.01
    assert plan.cash_after >= 0
    # Sells are listed first — the buys depend on their proceeds.
    assert all(a.side == "sell" for a in plan.actions[: len(sells)])


@pytest.mark.asyncio
async def test_a_buy_cannot_push_a_name_past_the_concentration_cap(seeded_session):
    user_id = await _user(seeded_session)
    # Already at the cap; the committee loving it must not make it bigger.
    await _hold(seeded_session, user_id, "XEQT.TO", 40, 5_000)
    await _hold(seeded_session, user_id, "ZAG.TO", 60, 5_000)
    await _cash(seeded_session, user_id, 5_000)

    svc = PortfolioActionsService(
        seeded_session,
        FakeProvider(),
        ScriptedLLM({"XEQT.TO": ("ADD", 0.9), "ZAG.TO": ("ADD", 0.9)}, default=("BUY", 0.9)),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))

    for buy in [a for a in plan.actions if a.side == "buy"]:
        assert buy.projected_weight <= CONCENTRATION_CAP + 0.01


@pytest.mark.asyncio
async def test_trades_below_the_minimum_are_not_proposed(seeded_session):
    """A $3 order costs more attention than the drift it corrects."""
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 5)  # nothing worth deploying

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({}, default=("HOLD", 0.7))
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))

    assert all(a.amount >= MIN_TRADE_CAD for a in plan.actions)


# --- doing nothing ---------------------------------------------------------
@pytest.mark.asyncio
async def test_holding_everything_is_a_valid_plan(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({}, default=("HOLD", 0.8))
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    assert plan.actions == []
    assert [h.symbol for h in plan.holds] == ["XEQT.TO"]
    assert "Doing nothing is a position" in plan.note


@pytest.mark.asyncio
async def test_planning_needs_holdings(seeded_session):
    from app.services.errors import ValidationFailed

    user_id = await _user(seeded_session)
    svc = PortfolioActionsService(seeded_session, FakeProvider(), ScriptedLLM({}))

    with pytest.raises(ValidationFailed):
        await svc.plan(user_id, ActionPlanIn())


@pytest.mark.asyncio
async def test_plan_still_works_with_the_llm_down(seeded_session):
    class Offline:
        async def complete(self, *a, **k):
            raise RuntimeError("offline")

        async def is_available(self) -> bool:
            return False

    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 100)

    svc = PortfolioActionsService(seeded_session, FakeProvider(), Offline())
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=2))

    assert plan.source == "rules"


# --- accepting -------------------------------------------------------------
@pytest.mark.asyncio
async def test_accepting_writes_proposed_orders_on_both_sides(seeded_session):
    user_id = await _user(seeded_session)
    svc = PortfolioActionsService(seeded_session, FakeProvider(), ScriptedLLM({}))

    proposal = await svc.accept(
        user_id,
        ActionAcceptIn(
            orders=[
                ActionOrderIn(symbol="XEQT.TO", side="sell", amount=250.0, price=100.0),
                ActionOrderIn(symbol="ZAG.TO", side="buy", amount=250.0, price=100.0),
            ],
            sizing="dollars",
        ),
    )

    assert proposal.total_sell == pytest.approx(250.0)
    assert proposal.total_buy == pytest.approx(250.0)
    # Recommend-only: an accepted plan is still just a checklist.
    assert all(o.status == "proposed" for o in proposal.orders)
    assert all(o.qty is None and o.notional == 250.0 for o in proposal.orders)


@pytest.mark.asyncio
async def test_every_holding_is_accounted_for(seeded_session):
    """No position may vanish from the plan.

    A holding that draws neither a sell nor a buy has to be reported as a
    deliberate hold — otherwise the user reads silence about their own money as
    approval, when it may just be that the budget could not stretch to it.
    """
    user_id = await _user(seeded_session)
    for symbol in ("XEQT.TO", "ZAG.TO", "VFV.TO"):
        await _hold(seeded_session, user_id, symbol, 10, 5_000)
    await _cash(seeded_session, user_id, 5)  # too little to fund any top-up

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({}, default=("ADD", 0.8))
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    accounted = {a.symbol for a in plan.actions} | {h.symbol for h in plan.holds}
    assert accounted == {"XEQT.TO", "ZAG.TO", "VFV.TO"}
    assert all("wasn't enough spare cash" in h.reason for h in plan.holds)


@pytest.mark.asyncio
async def test_a_small_budget_funds_few_meaningful_trades(seeded_session):
    """Conviction concentrates the budget instead of dusting it everywhere.

    With every name equally rated, the naive split is one tiny order per name —
    a page of trades that changes nothing and takes an afternoon to place.
    """
    user_id = await _user(seeded_session)
    for symbol in ("XEQT.TO", "ZAG.TO", "VFV.TO", "VEQT.TO"):
        await _hold(seeded_session, user_id, symbol, 10, 5_000)
    # ~1% of a $6,360 account: enough for one meaningful trade, not four.
    await _cash(seeded_session, user_id, 80)

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({}, default=("ADD", 0.6))
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    buys = [a for a in plan.actions if a.side == "buy"]
    assert len(buys) == 1
    assert buys[0].amount > MIN_TRADE_CAD


@pytest.mark.asyncio
async def test_source_reports_what_actually_ran(seeded_session):
    """A plan that mostly fell back to rules must not claim to be AI."""

    class FlakyLLM(ScriptedLLM):
        async def complete(self, prompt: str, **kwargs) -> str:
            if "ZAG.TO" in prompt:
                return _verdict("TRIM", 0.9)
            raise RuntimeError("upstream overloaded")

    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _hold(seeded_session, user_id, "ZAG.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(seeded_session, FakeProvider(), FlakyLLM({}))
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    assert plan.source == "mixed"


@pytest.mark.asyncio
async def test_an_ai_sell_is_never_redeployed_into_a_rules_only_buy(seeded_session):
    """Don't trade down in signal quality.

    Free-tier capacity comes and goes mid-plan, so one ticker gets a real
    committee and the next falls back to rules. Selling what the committee
    scrutinized to buy what only the rules saw is a switch justified by the
    weaker of the two signals — those proceeds stay in cash instead.

    The restriction is on the money, not on the existence of a buy. Cash on
    hand, and what a rule-driven trim raises, may still be invested; only what
    a judged sell raised is held back. Enforcing it by deleting every
    rules-sourced buy instead meant a half-reachable model produced a plan that
    only ever sold — see ``test_actions_buy_side.py``.
    """

    class HalfUpLLM(ScriptedLLM):
        async def complete(self, prompt: str, **kwargs) -> str:
            if "ZAG.TO" in prompt:
                return _verdict("TRIM", 0.9)  # a real verdict: sell it
            raise RuntimeError("upstream overloaded")  # everything else -> rules

    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "ZAG.TO", 10, 5_000)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(seeded_session, FakeProvider(), HalfUpLLM({}))
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))

    # ZAG was sold on a committee verdict; XEQT is trimmed by the concentration
    # rule, which exists precisely to redeploy.
    judged_sell = sum(a.amount for a in plan.actions if a.side == "sell" and a.symbol == "ZAG.TO")
    assert judged_sell > 0
    # Every cent of it survives as cash: the buys were funded from elsewhere.
    assert plan.cash_after >= judged_sell - 0.01
    assert "stays in cash" in plan.note
    assert "only partly reachable" in plan.note


@pytest.mark.asyncio
async def test_a_held_position_is_asked_the_holding_question(seeded_session):
    """The entry question must never drive an exit.

    "I would not open this today" is true of most things most of the time. Read
    as a sell it produces a standing recommendation to liquidate, so a held
    position is asked TRIM/HOLD/ADD instead — and a SKIP aimed at it is not a
    valid answer to that question.
    """
    seen: list[str] = []

    class Recorder(ScriptedLLM):
        async def complete(self, prompt: str, **kwargs) -> str:
            seen.append(prompt)
            return _verdict("SKIP", 0.95)  # the entry vocabulary — wrong question

    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(seeded_session, FakeProvider(), Recorder({}))
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    assert any("TRIM|HOLD|ADD" in p for p in seen), "held positions get the holding question"
    # A SKIP is not a verdict about a position you hold, so it cannot sell it.
    assert [a for a in plan.actions if a.side == "sell"] == []


@pytest.mark.asyncio
async def test_weights_are_measured_against_total_equity(seeded_session):
    """Current and projected weight must share a denominator.

    `portfolio()` weights positions against invested value; a plan compares them
    to post-trade weights against equity. Mixing the two overstates every
    position whenever cash is meaningful, and trims harder than the limit says.
    """
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)  # $1,590 invested
    await _cash(seeded_session, user_id, 1_590)  # half the account is cash

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({}, default=("HOLD", 0.8))
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0))

    (hold,) = plan.holds
    assert hold.weight == pytest.approx(0.5, abs=0.01)  # not 1.0


@pytest.mark.asyncio
async def test_share_sized_buys_carry_a_whole_share_count(seeded_session):
    """A share-sized buy must say how many shares — and mean it.

    The allocator used to ignore sizing entirely and hand back a dollar amount
    with no quantity, which the app rendered as "0 shares @ $187.60" next to a
    real dollar figure. Worse, the amount itself was unbuyable: a $94 slice of a
    $1,000 share is not a small buy, it is no buy.
    """
    user_id = await _user(seeded_session)
    # Three even positions, so no name is already at the concentration cap and
    # there is genuine room to add.
    for symbol in ("XEQT.TO", "ZAG.TO", "VFV.TO"):
        await _hold(seeded_session, user_id, symbol, 1, 5_000)
    await _cash(seeded_session, user_id, 1_000)

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({}, default=("ADD", 0.8))
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0, sizing="shares"))

    buys = [a for a in plan.actions if a.side == "buy"]
    assert buys, "expected a share-sized buy"
    for buy in buys:
        assert buy.qty is not None and buy.qty >= 1
        assert buy.qty == int(buy.qty)
        # The amount must be what those whole shares actually cost.
        assert buy.amount == pytest.approx(buy.qty * buy.price, abs=0.01)


@pytest.mark.asyncio
async def test_a_name_too_expensive_for_one_share_is_reported_not_proposed(seeded_session):
    user_id = await _user(seeded_session)
    for symbol in ("XEQT.TO", "ZAG.TO", "VFV.TO"):
        await _hold(seeded_session, user_id, symbol, 1, 5_000)
    # Enough to be worth a trade, nowhere near one $159 share.
    await _cash(seeded_session, user_id, 40)

    svc = PortfolioActionsService(
        seeded_session, FakeProvider(), ScriptedLLM({}, default=("ADD", 0.8))
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0, sizing="shares"))

    assert [a for a in plan.actions if a.side == "buy"] == []
    reasons = [h.reason for h in plan.holds] + [p.reason for p in plan.passed]
    assert any("one share is" in r for r in reasons)


@pytest.mark.asyncio
async def test_no_action_is_ever_zero_quantity(seeded_session):
    """The invariant behind both bugs: an order for nothing is not an order."""
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _hold(seeded_session, user_id, "ZAG.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 500)

    for sizing in ("shares", "dollars"):
        for verdict in ("ADD", "TRIM", "HOLD"):
            svc = PortfolioActionsService(
                seeded_session, FakeProvider(), ScriptedLLM({}, default=(verdict, 0.8))
            )
            plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=0, sizing=sizing))
            for a in plan.actions:
                assert a.amount > 0, f"{sizing}/{verdict}: {a.symbol} has no value"
                assert a.qty is None or a.qty > 0, f"{sizing}/{verdict}: {a.symbol} has qty {a.qty}"
