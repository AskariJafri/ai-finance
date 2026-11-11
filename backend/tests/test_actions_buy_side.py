"""Does the sell+buy planner ever actually say *buy*?

Scenario sweep written to answer one complaint: the plan comes back with sells
and nothing else. Each test isolates one mechanism that can suppress the buy
side, so a failing plan can be attributed rather than guessed at.
"""

from __future__ import annotations

import pytest

from app.schemas.invest import ActionPlanIn
from app.services.portfolio_actions_service import PortfolioActionsService
from tests.test_portfolio_actions import (  # reuse the established harness
    FakeProvider,
    ScriptedLLM,
    SignalProvider,
    _cash,
    _hold,
    _user,
    _verdict,
)


def _sides(plan):
    return (
        [(a.symbol, a.action, a.amount) for a in plan.actions if a.side == "sell"],
        [(a.symbol, a.action, a.amount) for a in plan.actions if a.side == "buy"],
    )


# --- 1. baseline: the committee likes new names and there is cash ----------
@pytest.mark.asyncio
async def test_buys_appear_when_the_committee_says_buy(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 5_000)

    svc = PortfolioActionsService(
        seeded_session,
        FakeProvider(),
        ScriptedLLM({"XEQT.TO": ("HOLD", 0.8)}, default=("BUY", 0.8)),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    sells, buys = _sides(plan)
    print("\n[1] sells=", sells, "\n    buys=", buys, "\n    note=", plan.note)
    assert buys, "cash on hand + BUY verdicts must produce buys"
    assert not sells


# --- 2. both sides in one plan --------------------------------------------
@pytest.mark.asyncio
async def test_a_trim_funds_a_buy_when_both_verdicts_are_ai(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _hold(seeded_session, user_id, "ZAG.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(
        seeded_session,
        FakeProvider(),
        ScriptedLLM({"XEQT.TO": ("TRIM", 0.9), "ZAG.TO": ("HOLD", 0.7)}, default=("BUY", 0.8)),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    sells, buys = _sides(plan)
    print("\n[2] sells=", sells, "\n    buys=", buys, "\n    note=", plan.note)
    assert sells and buys, "an AI sell should be redeployed into AI buys"


# --- 3. the reported symptom: candidates fell back to rules ---------------
@pytest.mark.asyncio
async def test_cash_still_buys_when_the_candidate_stage_runs_out_of_llm(seeded_session):
    """The bug this file was written for.

    Holdings are judged first, so when the endpoint runs dry the shopping list
    is the half that degrades to rules. The old signal-quality rule then
    deleted every one of those buys, and the plan came back as sells and
    nothing else — with $5,000 of idle cash sitting right there.

    Cash on hand was never the proceeds of anything, so it is free to be
    invested. Only what the committee-judged sell raised is held back.
    """

    class QuotaRunsOut(ScriptedLLM):
        """Answers the two held tickers, then 429s like a spent free tier."""

        async def complete(self, prompt: str, **kwargs) -> str:
            if "XEQT.TO" in prompt or "ZAG.TO" in prompt:
                return _verdict("TRIM" if "XEQT.TO" in prompt else "HOLD", 0.9)
            raise RuntimeError("429 rate limit exceeded")

    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _hold(seeded_session, user_id, "ZAG.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 5_000)

    svc = PortfolioActionsService(
        seeded_session, SignalProvider(declining={"XEQT.TO"}), QuotaRunsOut({})
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    sells, buys = _sides(plan)
    print("\n[3] sells=", sells, "\n    buys=", buys, "\n    cash_after=", plan.cash_after)
    print("    note=", plan.note)
    assert buys, "idle cash must still be investable when only the buy side degraded"
    # The sell was judged, so its proceeds are reserved — the buys came out of
    # the $5,000 that was already sitting in cash.
    judged_sell = sum(a.amount for a in plan.actions if a.side == "sell")
    assert plan.cash_after >= judged_sell - 0.01


# --- 4. AI fully offline: no AI sell, so the guardrail cannot bite --------
@pytest.mark.asyncio
async def test_rules_only_mode_can_still_buy(seeded_session):
    class Offline:
        model = "offline"

        async def complete(self, *a, **k):
            raise RuntimeError("offline")

        async def is_available(self) -> bool:
            return False

    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 5_000)

    svc = PortfolioActionsService(seeded_session, SignalProvider(), Offline())
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    sells, buys = _sides(plan)
    print("\n[4] source=", plan.source, "sells=", sells, "\n    buys=", buys)
    print("    note=", plan.note)
    assert buys, "a rules-only plan with cash should still be able to buy"


# --- 5. share sizing, the app's default when no broker is linked ----------
@pytest.mark.asyncio
async def test_share_sizing_on_a_modest_account(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 300)  # ~$1,890 equity, $300 spare

    svc = PortfolioActionsService(
        seeded_session,
        FakeProvider(),
        ScriptedLLM({"XEQT.TO": ("HOLD", 0.8)}, default=("BUY", 0.8)),
    )
    for sizing in ("dollars", "shares"):
        plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3, sizing=sizing))
        sells, buys = _sides(plan)
        print(f"\n[5] sizing={sizing} sells=", sells, "\n    buys=", buys)
        print("    passed=", [(p.symbol, p.action) for p in plan.passed][:4])


# --- 6. a held ADD verdict is a buy --------------------------------------
@pytest.mark.asyncio
async def test_add_on_a_held_name_produces_a_buy(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 5, 5_000)
    await _hold(seeded_session, user_id, "ZAG.TO", 5, 5_000)
    await _cash(seeded_session, user_id, 2_000)

    svc = PortfolioActionsService(
        seeded_session,
        FakeProvider(),
        ScriptedLLM({"XEQT.TO": ("ADD", 0.85), "ZAG.TO": ("HOLD", 0.6)}, default=("SKIP", 0.6)),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    sells, buys = _sides(plan)
    print("\n[6] sells=", sells, "\n    buys=", buys)
    assert buys and buys[0][0] == "XEQT.TO"


# --- 7. everything skipped: sells only, legitimately ----------------------
@pytest.mark.asyncio
async def test_trim_with_nothing_worth_buying(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _hold(seeded_session, user_id, "ZAG.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(
        seeded_session,
        FakeProvider(),
        ScriptedLLM({"XEQT.TO": ("TRIM", 0.9), "ZAG.TO": ("HOLD", 0.7)}, default=("SKIP", 0.7)),
    )
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    sells, buys = _sides(plan)
    print("\n[7] sells=", sells, "buys=", buys, "\n    note=", plan.note)
    assert sells and not buys


# --- 8. the budget is one deadline shared by both stages, held first ------
@pytest.mark.asyncio
async def test_the_candidate_stage_starves_when_the_held_stage_spends_the_deadline(
    seeded_session,
):
    """No exceptions, no outage — just a slow first stage.

    ``plan`` opens one ``llm_budget`` around both stages and judges holdings
    first, so waiting out a rate limit on a holding is time the shopping list
    never gets. Whichever side runs second is the side that degrades.
    """
    import time

    from app.ai.llm.budget import current_budget

    class SlowThenBroke(ScriptedLLM):
        def __init__(self):
            super().__init__({})
            self.answered: list[str] = []
            self.starved: list[str] = []

        async def complete(self, prompt: str, **kwargs) -> str:
            budget = current_budget()
            remaining = budget.remaining()
            symbol = prompt.split()[0] if prompt else "?"
            if remaining is not None and remaining <= 0:
                self.starved.append(symbol)
                raise RuntimeError("no budget left to wait for a slot")
            # Each held call waits out a rate limit; the deadline is real time.
            object.__setattr__(budget, "deadline", time.monotonic() - 1)
            self.answered.append(symbol)
            return _verdict("TRIM", 0.9)

    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 5_000)

    llm = SlowThenBroke()
    svc = PortfolioActionsService(seeded_session, FakeProvider(), llm)
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    sells, buys = _sides(plan)
    print("\n[8] answered=", len(llm.answered), "starved=", len(llm.starved))
    print("    sells=", sells, "buys=", buys, "source=", plan.source)
    print("    note=", plan.note)
    # This fake expires the deadline on *every* call, which is harsher than any
    # real endpoint. Even so the candidate stage now opens its own allowance and
    # gets a call away — under one shared budget it got none at all.
    assert len(llm.answered) >= 2, "stage 2 must get its own reserved allowance"


# --- 9. the guardrail only guards one direction --------------------------
@pytest.mark.asyncio
async def test_a_rules_only_sell_may_still_fund_an_ai_buy(seeded_session):
    """The mirror image of the guardrail — is it symmetric?

    A falling series makes the rules trim a holding with no committee involved.
    The candidates then get real verdicts. The proceeds of a decision nothing
    scrutinized go into a name that was scrutinized.
    """
    from datetime import date, timedelta

    from app.integrations.marketdata.provider import BarPoint, SymbolQuote

    class FallingProvider(FakeProvider):
        async def get_bars(self, symbol: str, start: date, end: date):
            if symbol.upper() == "USDCAD=X":
                return [BarPoint(start + timedelta(days=i), 1.4) for i in range(60)]
            if symbol.upper() == "XEQT.TO":  # the holding: straight down
                return [BarPoint(start + timedelta(days=i), 200.0 - i) for i in range(60)]
            return [BarPoint(start + timedelta(days=i), 100.0 + i) for i in range(60)]

        async def get_quote(self, symbol: str) -> SymbolQuote:
            currency = "CAD" if symbol.upper().endswith(".TO") else "USD"
            return SymbolQuote(symbol.upper(), 100.0, currency, f"{symbol.upper()} Inc")

    class AIOnlyForCandidates(ScriptedLLM):
        async def complete(self, prompt: str, **kwargs) -> str:
            if "XEQT.TO" in prompt:
                raise RuntimeError("no capacity for the holding")
            return _verdict("BUY", 0.8)

    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 0)

    svc = PortfolioActionsService(seeded_session, FallingProvider(), AIOnlyForCandidates({}))
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    sells, buys = _sides(plan)
    print("\n[9] sells=", sells, "\n    buys=", buys, "\n    source=", plan.source)
    print("    note=", plan.note)


# --- 10. a flat, choppy tape with no AI ----------------------------------
@pytest.mark.asyncio
async def test_rules_only_in_a_flat_market_buys_nothing(seeded_session):
    from datetime import date, timedelta

    from app.integrations.marketdata.provider import BarPoint

    class FlatProvider(FakeProvider):
        async def get_bars(self, symbol: str, start: date, end: date):
            if symbol.upper() == "USDCAD=X":
                return [BarPoint(start + timedelta(days=i), 1.4) for i in range(60)]
            return [
                BarPoint(start + timedelta(days=i), 100.0 + (1.0 if i % 2 else -1.0))
                for i in range(60)
            ]

    class Offline:
        model = "offline"

        async def complete(self, *a, **k):
            raise RuntimeError("offline")

        async def is_available(self) -> bool:
            return False

    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 3_000)

    svc = PortfolioActionsService(seeded_session, FlatProvider(), Offline())
    plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
    sells, buys = _sides(plan)
    print("\n[10] sells=", sells, "buys=", buys, "source=", plan.source)
    print("     note=", plan.note)


# --- 11. the new money rule, stated directly -----------------------------
@pytest.mark.asyncio
async def test_judged_sell_proceeds_are_reserved_but_cash_is_not(seeded_session):
    """The signal-quality rule applies to the swap, not to buying as such.

    One holding is sold on a real committee verdict while every candidate falls
    back to rules. The proceeds of that sell may not fund those buys — but the
    cash the account already had is nobody's proceeds, so it may.
    """

    class OnlyTheHolding(ScriptedLLM):
        async def complete(self, prompt: str, **kwargs) -> str:
            if "XEQT.TO" in prompt:
                return _verdict("TRIM", 0.9)
            raise RuntimeError("no capacity")

    user_id = await _user(seeded_session)
    # Three even positions, so nothing breaches the concentration cap — a
    # concentration trim is rule-driven money and free to redeploy, which would
    # muddy what this test is measuring.
    for symbol in ("XEQT.TO", "ZAG.TO", "VFV.TO"):
        await _hold(seeded_session, user_id, symbol, 10, 5_000)

    for cash, expect_buys in ((0.0, False), (4_000.0, True)):
        await _cash(seeded_session, user_id, cash)
        svc = PortfolioActionsService(
            seeded_session, SignalProvider(declining={"XEQT.TO"}), OnlyTheHolding({})
        )
        plan = await svc.plan(user_id, ActionPlanIn(max_new_positions=3))
        sells, buys = _sides(plan)
        print(f"\n[11] cash={cash} sells=", sells, "\n     buys=", buys)

        judged = sum(a.amount for a in plan.actions if a.side == "sell" and a.symbol == "XEQT.TO")
        # Always: what the committee decided to sell is not spent on what it
        # never saw.
        assert plan.cash_after >= judged - 0.01
        assert bool(buys) is expect_buys, (
            "with no cash there is nothing unrestricted to invest; with cash "
            "there is, and the plan must say so"
        )


@pytest.mark.asyncio
async def test_a_fully_funded_plan_does_not_claim_the_budget_was_starved():
    """`unfunded` must mean a real shortfall, never accumulated rounding.

    `headroom` is decremented by each allocation, so if the value compared
    against it were unrounded while the value subtracted from it were rounded,
    a fraction of a cent per buy would accumulate. At five buys that was enough
    to trip the flag, and the note then told the user the model had been only
    partly reachable and that a sell's proceeds were held back — on a plan with
    no sells in it at all.
    """
    from app.services.portfolio_actions_service import PortfolioActionsService

    svc = PortfolioActionsService(None, None, None)  # type: ignore[arg-type]
    equity, spendable = 14_680.0, 8_681.1288
    adds = [
        {
            "symbol": s,
            "name": s,
            "price_cad": 100.0,
            "confidence": c,
            "summary": "x",
            "current_value": 0.0,
            "label": "NEW",
            "verdict_source": "rules",
        }
        for s, c in [("A", 0.62), ("B", 0.61), ("C", 0.6), ("D", 0.59), ("E", 0.58)]
    ]
    purchases, _, unfunded = svc._allocate_buys(
        adds, spendable, equity, "dollars", weak_ceiling=spendable
    )
    assert len(purchases) == 5
    assert not unfunded
    # And the money is all accounted for, to the cent.
    assert sum(p.amount for p in purchases) == pytest.approx(spendable, abs=0.05)


@pytest.mark.asyncio
async def test_unfunded_still_fires_on_a_real_shortfall():
    """The flag has to keep working — a weak ceiling below the budget is the
    case it exists for."""
    from app.services.portfolio_actions_service import PortfolioActionsService

    svc = PortfolioActionsService(None, None, None)  # type: ignore[arg-type]
    adds = [
        {
            "symbol": s,
            "name": s,
            "price_cad": 100.0,
            "confidence": 0.6,
            "summary": "x",
            "current_value": 0.0,
            "label": "NEW",
            "verdict_source": "rules",
        }
        for s in ("A", "B")
    ]
    _, _, unfunded = svc._allocate_buys(adds, 4_000.0, 40_000.0, "dollars", weak_ceiling=500.0)
    assert unfunded
