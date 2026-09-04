"""The factor overlay where it meets real orders.

``test_factor_limits`` proves the policy; this proves the wiring — that a cut
the policy asks for actually reaches the order, the cash, the checklist and the
note, and that a portfolio the model cannot measure still gets a plan.

Stage 3 is driven directly with a hand-built model. Going through ``plan()``
would mean fabricating a year of non-collinear price history for six proxy
ETFs to assert something the regression is not what is under test here.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.analytics.factor_limits import Limits
from app.analytics.factor_risk import FactorModel
from app.schemas.invest import ActionPlanIn
from app.services.portfolio_actions_service import PortfolioActionsService

KEYS = ["equity", "ir", "fx_usdcad", "commodity", "credit"]


def _model(betas: dict[str, list[float]]) -> FactorModel:
    return FactorModel(
        keys=list(KEYS),
        cov=np.eye(len(KEYS)) * 1e-4,
        betas={s: np.array(b, dtype=float) for s, b in betas.items()},
        residual_var=dict.fromkeys(betas, 1e-6),
        factors_unavailable=[],
    )


CAD_ETF = [1.0, 0.0, 0.0, 0.0, 0.0]
US_STOCK = [1.0, 0.0, 1.0, 0.0, 0.0]

#: Generous everywhere except FX, so each test isolates one line.
LIMITS = Limits(
    fx_ceiling=0.50, share_ceiling=9.9, idio_ceiling=9.9, duration_min=-99, duration_max=99
)


def _service() -> PortfolioActionsService:
    """Stage 3 touches no session, provider or model — only arithmetic."""
    return PortfolioActionsService(None, None, None)  # type: ignore[arg-type]


def _held(symbol: str, value: float, equity: float, currency: str = "CAD") -> dict:
    return {
        "symbol": symbol,
        "market_value": value,
        "weight": round(value / equity, 4),
        "qty": value / 100.0,
        "unrealized_pl_pct": 0.0,
        "currency": currency,
        "name": f"{symbol} Inc",
    }


def _size(svc, *, equity, cash, held, candidates, model, sizing="dollars"):
    return svc._size(
        body=ActionPlanIn(sizing=sizing, max_new_positions=3),
        equity=equity,
        cash=cash,
        held=held,
        held_results={
            s: {"action": "HOLD", "confidence": 0.6, "summary": "steady.", "source": "ai"}
            for s in held
        },
        held_prices=dict.fromkeys(held, 100.0),
        held_names={s: f"{s} Inc" for s in held},
        candidates=candidates,
        passed=[],
        model=model,
        limits=LIMITS,
    )


def _candidate(symbol: str, confidence: float = 0.9) -> dict:
    return {
        "symbol": symbol,
        "name": f"{symbol} Inc",
        "action": "BUY",
        "confidence": confidence,
        "summary": "The committee would open this today.",
        "source": "ai",
        "price_cad": 100.0,
        "currency": "USD",
    }


@pytest.mark.asyncio
async def test_a_us_buy_is_sized_down_and_the_cash_stays_uncommitted():
    """$3k of a US name is 0.30 of FX in a $10k book. Without the ceiling the
    plan spends $3,800 of the $4,000 cash — the rest is the 2% buffer — on
    another US name and lands at 0.68. The 0.50 ceiling allows $2,000 of it."""
    equity = 10_000.0
    held = {
        "CADX.TO": _held("CADX.TO", 3_000.0, equity),
        "USSTK": _held("USSTK", 3_000.0, equity, "USD"),
    }
    sized = _size(
        _service(),
        equity=equity,
        cash=4_000.0,
        held=held,
        candidates=[_candidate("USNEW")],
        model=_model({"CADX.TO": CAD_ETF, "USSTK": US_STOCK, "USNEW": US_STOCK}),
    )

    buys = [a for a in sized.actions if a.side == "buy"]
    assert [b.symbol for b in buys] == ["USNEW"]
    # $1,900 rather than the $2,000 the line allows exactly: the overlay tries
    # fractions of the proposed buy on a 5% grid and takes the largest that
    # clears, so it errs on the side of buying too little.
    assert buys[0].amount == pytest.approx(1_900.0)
    assert sized.factors is not None
    assert sized.factors.projected_exposure("fx_usdcad") <= 0.50 + 1e-9
    assert "USD/CAD exposure limit" in buys[0].reason
    # Cash the overlay declined to spend is cash, not a rounding error.
    assert sized.cash_after == pytest.approx(4_000.0 - buys[0].amount, abs=1.0)

    assert sized.factors is not None and sized.factors.enforced
    breach = next(b for b in sized.factors.breaches if b.key == "fx_usdcad")
    assert breach.binding
    assert breach.current == pytest.approx(0.30)


@pytest.mark.asyncio
async def test_the_plan_says_why_the_money_stayed_in_cash():
    equity = 10_000.0
    held = {
        "CADX.TO": _held("CADX.TO", 3_000.0, equity),
        "USSTK": _held("USSTK", 3_000.0, equity, "USD"),
    }
    sized = _size(
        _service(),
        equity=equity,
        cash=4_000.0,
        held=held,
        candidates=[_candidate("USSTK")],
        model=_model({"CADX.TO": CAD_ETF, "USSTK": US_STOCK}),
    )
    note = PortfolioActionsService._note(
        sized.actions, sized.holds, sized.cash_after, ActionPlanIn(), False, sized.factors
    )
    # The constraint is the app's, so it is stated as the app's — not folded
    # into the committee's reasoning where it would read as a market view.
    assert "USD/CAD" in note


@pytest.mark.asyncio
async def test_a_buy_cut_below_one_share_is_reported_rather_than_dropped():
    """Under share sizing a cut can leave less than one share. That is still a
    decision about the user's money, so it has to appear somewhere."""
    equity = 10_000.0
    held = {
        "CADX.TO": _held("CADX.TO", 3_000.0, equity),
        "USSTK": _held("USSTK", 4_900.0, equity, "USD"),
    }
    passed: list = []
    svc = _service()
    sized = svc._size(
        body=ActionPlanIn(sizing="shares"),
        equity=equity,
        cash=2_000.0,
        held=held,
        held_results={
            s: {"action": "HOLD", "confidence": 0.6, "summary": "steady.", "source": "ai"}
            for s in held
        },
        held_prices=dict.fromkeys(held, 100.0),
        held_names={s: f"{s} Inc" for s in held},
        candidates=[_candidate("USSTK")],
        passed=passed,
        model=_model({"CADX.TO": CAD_ETF, "USSTK": US_STOCK}),
        limits=LIMITS,
    )
    spoken_for = (
        {a.symbol for a in sized.actions}
        | {h.symbol for h in sized.holds}
        | {p.symbol for p in passed}
    )
    assert "USSTK" in spoken_for


@pytest.mark.asyncio
async def test_a_plan_the_model_cannot_measure_is_still_a_plan():
    """No factor model is not an error. The limits stand down and say so."""
    equity = 10_000.0
    held = {"CADX.TO": _held("CADX.TO", 6_000.0, equity)}
    sized = _size(
        _service(),
        equity=equity,
        cash=4_000.0,
        held=held,
        candidates=[_candidate("USSTK")],
        model=None,
    )
    assert [a.symbol for a in sized.actions if a.side == "buy"] == ["USSTK"]
    assert sized.factors is not None
    assert not sized.factors.enforced
    assert sized.factors.reason and "Sync data" in sized.factors.reason


@pytest.mark.asyncio
async def test_a_plan_inside_the_limits_is_the_plan_the_committee_asked_for():
    equity = 10_000.0
    held = {"CADX.TO": _held("CADX.TO", 6_000.0, equity)}
    sized = _size(
        _service(),
        equity=equity,
        cash=4_000.0,
        held=held,
        candidates=[_candidate("CADY.TO")],
        model=_model({"CADX.TO": CAD_ETF, "CADY.TO": CAD_ETF}),
    )
    buys = [a for a in sized.actions if a.side == "buy"]
    assert [b.symbol for b in buys] == ["CADY.TO"]
    assert sized.factors is not None and not sized.factors.enforced
    # An untouched order keeps the committee's words and gains no house note.
    assert buys[0].reason == "The committee would open this today."
