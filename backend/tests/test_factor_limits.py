"""The factor overlay: what it may cut, and what it must not.

The model is built by hand rather than regressed, so every expected number is
arithmetic anyone can check. A book worth $10,000 holding $3,000 of a name with
an FX beta of 1.0 carries 0.30 of USD/CAD exposure — no fitting involved — and
that is what makes it possible to assert the overlay cut the *right* amount
rather than merely some amount.

The properties worth pinning down are the ones that would be dangerous to get
wrong: it may only ever buy less, it may never invent a sell, and a limit that
was already breached before the plan existed must not be charged to the plan.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.analytics import factor_limits as fl
from app.analytics.factor_risk import FactorModel

KEYS = ["equity", "ir", "fx_usdcad", "commodity", "credit"]
DAILY_VAR = 1e-4  # 1% daily, ~15.9% annualized


def _model(betas: dict[str, list[float]], residual: dict[str, float] | None = None) -> FactorModel:
    """A model with independent, equally volatile factors.

    Diagonal covariance is not realism, it is legibility: with no cross terms a
    factor's variance contribution is exactly ``(w*beta)^2 * var``, so a test
    can state the answer instead of trusting the implementation for it.
    """
    residual = residual or {}
    return FactorModel(
        keys=list(KEYS),
        cov=np.eye(len(KEYS)) * DAILY_VAR,
        betas={s: np.array(b, dtype=float) for s, b in betas.items()},
        residual_var={s: residual.get(s, 1e-6) for s in betas},
        factors_unavailable=[],
    )


#: equity, ir, fx, commodity, credit
CAD_ETF = [1.0, 0.0, 0.0, 0.0, 0.0]
US_ETF = [1.0, 0.0, 1.0, 0.0, 0.0]  # a CAD holder of a US listing bears the currency
LONG_BOND = [0.0, 1.5, 0.0, 0.0, 0.0]  # 1.5 * 7.5y proxy = 11.25 years


def _limits(**over) -> fl.Limits:
    base = {
        "fx_ceiling": 0.50,
        "share_ceiling": 0.85,
        "idio_ceiling": 0.70,
        "duration_min": -2.0,
        "duration_max": 8.0,
    }
    return fl.Limits(**{**base, **over})


# --- projection --------------------------------------------------------------


def test_cash_dilutes_factor_exposure_rather_than_being_normalised_away():
    """Half a book in cash carries half the currency risk.

    Renormalising to the invested part would report that deploying the cash
    changed nothing, which is the one answer that cannot be right.
    """
    model = _model({"USSTK": US_ETF})
    half = model.project({"USSTK": 0.5})
    full = model.project({"USSTK": 1.0})
    assert half is not None and full is not None
    assert half.exposures["fx_usdcad"] == pytest.approx(0.5)
    assert full.exposures["fx_usdcad"] == pytest.approx(1.0)


def test_exposures_aggregate_linearly_across_holdings():
    model = _model({"CADX.TO": CAD_ETF, "USSTK": US_ETF})
    state = model.project({"CADX.TO": 0.3, "USSTK": 0.3})
    assert state is not None
    assert state.exposures["equity"] == pytest.approx(0.6)
    assert state.exposures["fx_usdcad"] == pytest.approx(0.3)


def test_ir_exposure_is_reported_as_duration_years():
    model = _model({"BOND": LONG_BOND})
    state = model.project({"BOND": 1.0})
    assert state is not None
    assert state.duration_years == pytest.approx(11.25)


def test_covered_weight_reports_what_the_model_could_not_price():
    model = _model({"CADX.TO": CAD_ETF})
    state = model.project({"CADX.TO": 0.4, "MYSTERY": 0.4})
    assert state is not None
    assert state.covered_weight == pytest.approx(0.5)


# --- the fx ceiling ----------------------------------------------------------


def test_fx_ceiling_cuts_a_us_buy_back_to_the_limit():
    """$3k of a US name in a $10k book is 0.30 of FX. A $4k top-up would make
    it 0.70; the 0.50 ceiling allows exactly $2k of it."""
    model = _model({"CADX.TO": CAD_ETF, "USSTK": US_ETF})
    result = fl.constrain(
        model,
        equity=10_000.0,
        positions={"CADX.TO": 3_000.0, "USSTK": 3_000.0},
        buys={"USSTK": 4_000.0},
        limits=_limits(),
    )
    assert result.buys["USSTK"] == pytest.approx(2_000.0)
    assert result.state_after is not None
    assert result.state_after.exposures["fx_usdcad"] == pytest.approx(0.50)

    breach = next(b for b in result.breaches if b.key == "fx_usdcad")
    assert breach.binding
    assert breach.current == pytest.approx(0.30)
    assert breach.projected == pytest.approx(0.70)
    assert "USSTK" in breach.message


def test_a_cad_buy_is_left_alone_when_the_us_buy_is_the_problem():
    """The overlay must cut what causes the breach, not what is convenient."""
    model = _model({"CADX.TO": CAD_ETF, "USSTK": US_ETF})
    result = fl.constrain(
        model,
        equity=10_000.0,
        positions={"CADX.TO": 3_000.0, "USSTK": 3_000.0},
        buys={"USSTK": 4_000.0, "CADX.TO": 1_000.0},
        limits=_limits(),
    )
    assert result.buys["CADX.TO"] == pytest.approx(1_000.0)
    assert result.buys["USSTK"] < 4_000.0


def test_a_plan_inside_every_limit_is_returned_untouched():
    """Equity and a bond sleeve carrying comparable risk: nothing near a line."""
    model = _model({"CADX.TO": CAD_ETF, "BOND": [0.0, 1.0, 0.0, 0.0, 0.0]})
    result = fl.constrain(
        model,
        equity=10_000.0,
        positions={"CADX.TO": 3_000.0, "BOND": 3_000.0},
        buys={"CADX.TO": 1_000.0},
        limits=_limits(),
    )
    assert result.buys == {"CADX.TO": 1_000.0}
    assert result.breaches == []
    assert not result.enforced


# --- what the overlay may never do -------------------------------------------


def test_the_overlay_can_only_ever_buy_less():
    """No limit may raise a buy, and none may reach the positions at all.

    This is the property that keeps a modelling artifact from becoming a
    taxable event: the worst a wrong exposure can do here is leave money in
    cash.
    """
    model = _model({"CADX.TO": CAD_ETF, "USSTK": US_ETF, "BOND": LONG_BOND})
    positions = {"CADX.TO": 2_000.0, "USSTK": 3_000.0, "BOND": 2_000.0}
    before = dict(positions)
    buys = {"USSTK": 3_000.0, "BOND": 2_000.0, "CADX.TO": 500.0}
    result = fl.constrain(model, equity=12_000.0, positions=positions, buys=buys, limits=_limits())
    assert positions == before  # untouched: nothing here sizes a sell
    for symbol, amount in buys.items():
        assert 0.0 <= result.buys[symbol] <= amount + 1e-9


def test_a_breach_the_plan_did_not_cause_is_reported_not_acted_on():
    """The book is already past the FX ceiling and the plan only buys Canadian.

    Cutting that buy would not lower the exposure by a cent — it would just
    make the app look like it had done something.
    """
    model = _model({"CADX.TO": CAD_ETF, "USSTK": US_ETF})
    result = fl.constrain(
        model,
        equity=10_000.0,
        positions={"USSTK": 7_000.0},
        buys={"CADX.TO": 2_000.0},
        limits=_limits(),
    )
    assert result.buys["CADX.TO"] == pytest.approx(2_000.0)
    breach = next(b for b in result.breaches if b.key == "fx_usdcad")
    assert not breach.binding
    assert not result.enforced
    assert "does not make it worse" in breach.message


def test_nothing_is_enforced_when_the_model_prices_too_little_of_the_book():
    """Thin coverage is the model's gap, not the portfolio's problem."""
    model = _model({"CADX.TO": CAD_ETF})
    result = fl.constrain(
        model,
        equity=10_000.0,
        # 80% of the book is a symbol the regression never solved.
        positions={"CADX.TO": 2_000.0, "MYSTERY": 8_000.0},
        buys={"CADX.TO": 5_000.0},
        limits=_limits(fx_ceiling=0.0),
    )
    assert result.buys == {"CADX.TO": 5_000.0}
    assert result.breaches == []
    assert result.state_now is None


def test_no_model_means_no_overlay():
    result = fl.constrain(
        None, equity=10_000.0, positions={"X": 1.0}, buys={"X": 5.0}, limits=_limits()
    )
    assert result.buys == {"X": 5.0}
    assert result.breaches == []


# --- the other three limits --------------------------------------------------


def test_duration_band_catches_a_rates_bet_taken_as_a_side_effect():
    """Nobody asked for 11 years of duration; it arrives with the bond fund."""
    model = _model({"CADX.TO": CAD_ETF, "BOND": LONG_BOND})
    result = fl.constrain(
        model,
        equity=10_000.0,
        positions={"CADX.TO": 2_000.0},
        buys={"BOND": 8_000.0},
        limits=_limits(),
    )
    breach = next(b for b in result.breaches if b.key == "duration")
    assert breach.binding
    assert breach.projected == pytest.approx(9.0)  # 0.8 * 1.5 * 7.5
    assert result.buys["BOND"] < 8_000.0
    after = result.state_after
    assert after is not None and after.duration_years is not None
    assert after.duration_years <= 8.0 + 1e-9


def test_a_binding_limit_already_breached_refuses_only_what_makes_it_worse():
    """Past the FX ceiling already: the US buy is refused, the Canadian one is
    not. The limit constrains the plan, it does not cancel it."""
    model = _model({"CADX.TO": CAD_ETF, "USSTK": US_ETF})
    result = fl.constrain(
        model,
        equity=10_000.0,
        positions={"USSTK": 6_000.0},  # fx 0.60, already past the 0.50 ceiling
        buys={"USSTK": 1_000.0, "CADX.TO": 3_000.0},
        limits=_limits(),
    )
    assert result.buys["CADX.TO"] == pytest.approx(3_000.0)
    assert result.buys["USSTK"] == pytest.approx(0.0)
    after = result.state_after
    # No worse than it started: the plan is not charged for the existing breach.
    assert after is not None
    assert after.exposures["fx_usdcad"] == pytest.approx(0.60)


def test_equity_share_of_risk_fires_when_more_names_are_not_more_diversification():
    """Two tickers, one factor. The weights look diversified; the risk is not."""
    model = _model({"CADX.TO": CAD_ETF, "OTHER.TO": CAD_ETF}, residual={"CADX.TO": 1e-9})
    result = fl.constrain(
        model,
        equity=10_000.0,
        positions={"CADX.TO": 4_000.0},
        buys={"OTHER.TO": 4_000.0},
        limits=_limits(share_ceiling=0.85, fx_ceiling=99.0, duration_max=99.0),
    )
    breach = next((b for b in result.breaches if b.key == "equity_share"), None)
    assert breach is not None
    # Everything loads on one factor, so its share of factor variance is total.
    assert breach.projected == pytest.approx(1.0)


def test_idiosyncratic_ceiling_reports_a_single_name_bet_without_blocking_it():
    """A holding whose risk is mostly its own is a bet on that company alone —
    and the app says so without cancelling the order.

    Single-name risk is a share of the total, so it moves whenever cash is
    deployed into anything. Capping it would mean the only allowed plan is to
    stay in cash. See ``_Limit.binds``.
    """
    model = _model(
        {"CADX.TO": CAD_ETF, "WILDCARD": [0.05, 0.0, 0.0, 0.0, 0.0]},
        # Residual variance an order of magnitude above the factors'.
        residual={"WILDCARD": 1e-3, "CADX.TO": 1e-9},
    )
    result = fl.constrain(
        model,
        equity=10_000.0,
        positions={"CADX.TO": 4_000.0},
        buys={"WILDCARD": 5_000.0},
        limits=_limits(fx_ceiling=99.0, duration_max=99.0, share_ceiling=99.0),
    )
    breach = next(b for b in result.breaches if b.key == "idiosyncratic")
    assert not breach.binding
    assert breach.projected > 0.70
    assert result.buys["WILDCARD"] == pytest.approx(5_000.0)
    assert "No order was changed" in breach.message


# --- the brief the committee is shown ----------------------------------------


def test_brief_reports_the_same_numbers_the_overlay_enforces():
    model = _model({"USSTK": US_ETF})
    state = model.project({"USSTK": 0.6})
    brief = fl.brief(state, _limits())
    assert brief is not None
    block = brief.block()
    # Ours, so it is labelled authoritative — unlike news, which is not.
    assert "Authoritative" in block
    assert "USD/CAD exposure" in brief.over  # 0.60 is past the 0.50 ceiling
    # It informs a verdict; it never becomes an instruction to sell.
    # The block steers between positions; it never becomes a blanket refusal.
    # Written one-directionally ("adding to this needs a stronger case") it read
    # as a bar on every candidate at once — on a book that is 96% equity risk,
    # every stock is more of the dominant factor — and the planner answered a
    # request to invest $500 by selling $8,000 into cash.
    assert "never on its own a reason to decline a position or to sell one" in block
    assert "clears a LOWER bar" in block
    assert "not to refuse them" in block


def test_brief_leaves_out_factors_that_are_not_moving_anything():
    model = _model({"CADX.TO": CAD_ETF})
    brief = fl.brief(model.project({"CADX.TO": 1.0}), _limits())
    assert brief is not None
    names = {line.name for line in brief.lines}
    assert names == {"Equity market"}


def test_brief_is_none_without_a_state():
    assert fl.brief(None, _limits()) is None
