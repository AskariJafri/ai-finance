"""Risk math verified against hand-computed values.

The 6-point fixture: closes [100, 102, 101, 105, 103, 106]
Simple returns r = [0.02, -0.00980392..., 0.03960396..., -0.01904762..., 0.02912621...]
(hand-derived in comments where asserted).
"""

from __future__ import annotations

import numpy as np
import pytest

from app.analytics import invest_math as im

CLOSES = np.array([100.0, 102.0, 101.0, 105.0, 103.0, 106.0])
R = im.returns_from_closes(CLOSES)


def test_returns_from_closes():
    # 102/100-1 = 0.02 ; 101/102-1 = -0.00980392...
    assert R[0] == pytest.approx(0.02)
    assert R[1] == pytest.approx(-1 / 102, abs=1e-12)
    assert len(R) == 5


def test_annualized_vol_matches_hand_computation():
    # std(R, ddof=1) * sqrt(252), computed with numpy as the reference —
    # MIN_DAYS guard bypassed by monkeypatching not needed: call the pieces.
    sd = float(np.std(R, ddof=1))
    expected = sd * np.sqrt(252)
    # len(R)=5 < MIN_DAYS so the public fn returns None; verify the guard...
    assert im.annualized_vol(R) is None
    # ...and the formula itself on a long-enough series (tile the fixture).
    long_r = np.tile(R, 20)  # 100 points
    assert im.annualized_vol(long_r) == pytest.approx(
        float(np.std(long_r, ddof=1)) * np.sqrt(252), abs=1e-12
    )
    assert expected > 0  # sanity


def test_sharpe_and_sortino():
    long_r = np.tile(R, 20)
    rf = 0.04
    vol = float(np.std(long_r, ddof=1)) * np.sqrt(252)
    expected_sharpe = (float(np.mean(long_r)) * 252 - rf) / vol
    assert im.sharpe(long_r, rf) == pytest.approx(expected_sharpe, abs=1e-12)

    rf_d = rf / 252
    downside = np.minimum(long_r - rf_d, 0.0)
    dd = np.sqrt(252 * np.mean(downside**2))
    expected_sortino = (float(np.mean(long_r)) * 252 - rf) / dd
    assert im.sortino(long_r, rf) == pytest.approx(expected_sortino, abs=1e-12)


def test_max_drawdown_hand_traced():
    # Equity path of CLOSES normalized: peak 105 at i=3, trough 103 at i=4:
    # drawdown = 103/105 - 1 = -0.019047...
    assert im.max_drawdown(R) == pytest.approx(103 / 105 - 1, abs=1e-12)


def test_max_drawdown_monotonic_up_is_zero():
    r = np.array([0.01] * 10)
    assert im.max_drawdown(r) == pytest.approx(0.0)


def test_beta_of_series_with_itself_is_one():
    long_r = np.tile(R, 20)
    assert im.beta(long_r, long_r) == pytest.approx(1.0, abs=1e-12)
    # Scaling returns by 2 doubles beta.
    assert im.beta(2 * long_r, long_r) == pytest.approx(2.0, abs=1e-12)


def test_var_cvar_hand_computed():
    # 100 returns: 95 zeros and 5 losses of -1%..-5%. 5th-percentile quantile
    # (linear interpolation) sits inside the loss tail.
    r = np.array([0.0] * 95 + [-0.01, -0.02, -0.03, -0.04, -0.05])
    var, cvar = im.hist_var_cvar(r, 0.95)
    q = float(np.quantile(r, 0.05))
    assert var == pytest.approx(-q, abs=1e-12)
    tail = r[r <= q]
    assert cvar == pytest.approx(-float(np.mean(tail)), abs=1e-12)
    assert cvar >= var  # expected shortfall is at least VaR


def test_hhi_and_effective_n():
    assert im.hhi([0.25, 0.25, 0.25, 0.25]) == pytest.approx(0.25)
    assert im.hhi([1.0]) == pytest.approx(1.0)
    # 60/40: 0.36+0.16 = 0.52
    assert im.hhi([0.6, 0.4]) == pytest.approx(0.52)


def test_constant_prices_degenerate():
    flat = np.zeros(100)
    assert im.annualized_vol(flat) == pytest.approx(0.0)
    assert im.sharpe(flat, 0.04) is None  # zero vol -> undefined
    assert im.beta(flat, flat) is None  # zero benchmark variance


def test_short_series_returns_none():
    short = np.array([0.01] * 10)
    assert im.annualized_vol(short) is None
    assert im.sharpe(short, 0.04) is None
    assert im.hist_var_cvar(short) is None


def test_align_series_intersects_dates():
    from datetime import date

    a = [(date(2026, 1, i), 100.0 + i) for i in range(1, 6)]
    b = [(date(2026, 1, i), 200.0 + i) for i in range(3, 8)]
    dates, closes = im.align_series({"A": a, "B": b})
    assert dates == [date(2026, 1, 3), date(2026, 1, 4), date(2026, 1, 5)]
    assert list(closes["A"]) == [103.0, 104.0, 105.0]
    assert list(closes["B"]) == [203.0, 204.0, 205.0]


def test_portfolio_returns_weighted_sum():
    r = {"A": np.array([0.02, -0.01]), "B": np.array([0.01, 0.03])}
    rp = im.portfolio_returns({"A": 0.5, "B": 0.5}, r)
    assert rp[0] == pytest.approx(0.015)
    assert rp[1] == pytest.approx(0.01)


def test_money_weighted_irr_single_contribution():
    # $1000 for exactly 1 year ending at $1100 -> IRR 10%.
    irr = im.money_weighted_irr([(1000.0, 1.0)], 1100.0)
    assert irr == pytest.approx(0.10, abs=1e-4)


def test_cagr():
    assert im.cagr(100.0, 200.0, 10.0) == pytest.approx(2 ** (1 / 10) - 1, abs=1e-12)
    assert im.cagr(0.0, 100.0, 1.0) is None
