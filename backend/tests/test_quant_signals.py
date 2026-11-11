"""The house signal and the rule that anchors the committee to it."""

from __future__ import annotations

import math

import pytest

from app.analytics.quant_signals import (
    ADD_ENTRY,
    BUY_ENTRY,
    TRIM_EXIT,
    QuantSignal,
    alpha_weight,
    compute_signal,
    reconcile,
    vol_capped_weight,
)


def _series(n: int, drift: float, noise: float = 0.01, start: float = 100.0) -> list[float]:
    """Deterministic noisy series: ``drift`` per day, ~15% annualised noise."""
    return [start * (1 + drift * i) * (1 + noise * math.sin(1.3 * i)) for i in range(n)]


def _crash(n: int = 300) -> list[float]:
    """A year-long uptrend that then loses a third in two months."""
    up = _series(n - 45, 0.0008)
    top = up[-1]
    return up + [top * (1 - 0.009 * (i + 1)) for i in range(45)]


# --- the signal --------------------------------------------------------------
def test_a_steady_uptrend_is_a_buy_and_a_hold_or_add():
    s = compute_signal(_series(300, 0.0008))
    assert s is not None
    assert s.score >= BUY_ENTRY
    assert s.above_sma200 is True
    assert s.decide(held=False) == "BUY"
    assert s.decide(held=True) in {"ADD", "HOLD"}


def test_a_steady_downtrend_is_skipped_and_trimmed():
    s = compute_signal(_series(300, -0.0008))
    assert s is not None
    assert s.score <= TRIM_EXIT
    assert s.above_sma200 is False
    assert s.decide(held=False) == "SKIP"
    assert s.decide(held=True) == "TRIM"


def test_a_crash_below_the_200_day_trips_the_trailing_stop():
    s = compute_signal(_crash())
    assert s is not None
    assert s.stop_hit
    assert s.decide(held=True, symbol="ACME") == "TRIM"
    assert s.full_exit("ACME")


def test_a_broad_index_fund_is_never_stopped_out():
    s = compute_signal(_crash())
    assert s is not None and s.stop_hit
    # Same prices, but a diversified index: no stop, and no full exit.
    assert not s.full_exit("XEQT.TO")
    assert s.decide(held=True, symbol="XEQT.TO") in {"HOLD", "TRIM"}
    if s.decide(held=True, symbol="XEQT.TO") == "TRIM":
        assert s.score <= -0.5


def test_overbought_is_not_a_sell_signal():
    """The old rule exited at RSI >= 80. Strength is what momentum looks like."""
    s = compute_signal(_series(300, 0.0015))
    assert s is not None
    assert s.decide(held=True) != "TRIM"


def test_too_little_history_gives_no_signal():
    assert compute_signal(_series(40, 0.001)) is None


def test_the_signal_is_a_pure_function_of_the_closes():
    closes = _series(300, 0.0004)
    assert compute_signal(closes) == compute_signal(list(closes))


def test_the_no_trade_band_holds_between_the_thresholds():
    middle = QuantSignal(score=(TRIM_EXIT + ADD_ENTRY) / 2, above_sma200=True)
    assert middle.decide(held=True) == "HOLD"
    # Just under the add line and just over the trim line: still HOLD.
    assert QuantSignal(score=ADD_ENTRY - 0.01, above_sma200=True).decide(held=True) == "HOLD"
    assert QuantSignal(score=TRIM_EXIT + 0.01, above_sma200=False).decide(held=True) == "HOLD"


# --- sizing ------------------------------------------------------------------
def test_a_volatile_name_gets_a_smaller_ceiling():
    assert vol_capped_weight(0.60, 0.40, "ACME") < vol_capped_weight(0.20, 0.40, "ACME")
    assert vol_capped_weight(0.60, 0.40, "ACME") == pytest.approx(0.035 / 0.60)
    # Index funds are capped by the flat limit only.
    assert vol_capped_weight(0.60, 0.40, "VFV.TO") == 0.40


def test_buys_are_weighted_by_score_per_unit_of_risk():
    # Same score, double the vol: half the weight.
    assert alpha_weight(0.5, 0.40) == pytest.approx(alpha_weight(0.5, 0.20) / 2)


# --- reconciling the committee ----------------------------------------------
def _sig(score: float, above: bool | None = True, stop: bool = False) -> QuantSignal:
    return QuantSignal(score=score, above_sma200=above, stop_hit=stop)


def test_the_committee_may_move_a_holding_one_step():
    assert reconcile("HOLD", "ADD", _sig(0.2), held=True) == ("ADD", None)
    assert reconcile("HOLD", "TRIM", _sig(0.0, above=False), held=True) == ("TRIM", None)


def test_the_committee_may_not_move_a_holding_two_steps():
    final, why = reconcile("ADD", "TRIM", _sig(0.6), held=True)
    assert final == "HOLD" and why
    final, why = reconcile("TRIM", "ADD", _sig(-0.6, above=False), held=True)
    assert final == "HOLD" and why


def test_the_trailing_stop_cannot_be_talked_out_of():
    final, why = reconcile("TRIM", "HOLD", _sig(-0.4, above=False, stop=True), held=True)
    assert final == "TRIM" and why


def test_no_selling_into_a_strong_uptrend_on_one_view():
    final, why = reconcile("HOLD", "TRIM", _sig(ADD_ENTRY + 0.05), held=True)
    assert final == "HOLD" and why


def test_no_adding_to_a_broken_trend():
    final, why = reconcile("HOLD", "ADD", _sig(0.1, above=False), held=True)
    assert final == "HOLD" and why


def test_the_committee_can_always_veto_a_buy():
    assert reconcile("BUY", "SKIP", _sig(0.8), held=False) == ("SKIP", None)


def test_the_committee_can_promote_only_a_near_buy():
    assert reconcile("SKIP", "BUY", _sig(0.15), held=False) == ("BUY", None)
    final, why = reconcile("SKIP", "BUY", _sig(-0.2), held=False)
    assert final == "SKIP" and why
    final, why = reconcile("SKIP", "BUY", _sig(0.2, above=False), held=False)
    assert final == "SKIP" and why
