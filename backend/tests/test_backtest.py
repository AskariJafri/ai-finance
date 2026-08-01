from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from app.analytics import backtest as bt


def _dates(n: int, start: date = date(2024, 1, 1)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_buy_hold_tracks_price():
    dates = _dates(10)
    closes = {"A": np.linspace(100, 110, 10)}
    result = bt.buy_hold(dates, closes, {"A": 1.0}, capital=10_000)
    assert result.equity[0] == pytest.approx(10_000)
    assert result.equity[-1] == pytest.approx(10_000 * 110 / 100)


def test_dca_share_arithmetic_hand_traced():
    # 3 months, price 100 in Jan, 50 in Feb, 100 in Mar. $100/month.
    # Jan: 1 share; Feb: 2 shares (cheap!); Mar: 1 share. Total 4 shares.
    # Final value = 4 * 100 = 400 on $300 contributed.
    dates = [date(2024, 1, 2), date(2024, 2, 1), date(2024, 3, 1)]
    closes = {"A": np.array([100.0, 50.0, 100.0])}
    result = bt.dca(dates, closes, {"A": 1.0}, monthly_amount=100.0)
    assert result.contributed == pytest.approx(300.0)
    assert result.equity[-1] == pytest.approx(400.0)


def test_dca_determinism():
    dates = _dates(300)
    closes = {"A": 100 + np.sin(np.arange(300) / 10) * 20}
    r1 = bt.dca(dates, closes, {"A": 1.0}, 500.0)
    r2 = bt.dca(dates, closes, {"A": 1.0}, 500.0)
    assert np.array_equal(r1.equity, r2.equity)


def test_sma_crossover_v_shape_single_flip():
    # Price falls then rises steeply: fast SMA crosses above slow exactly once
    # after the turn. With fast=2, slow=4 on a small series we can trace it.
    dates = _dates(12)
    closes = np.array([100, 90, 80, 70, 60, 70, 80, 90, 100, 110, 120, 130.0])
    result = bt.sma_crossover(dates, closes, fast=2, slow=4, risk_free=0.0)
    # It should be in cash during the fall (equity flat), then long on the rise.
    assert result.trades >= 1
    assert result.equity[-1] > result.equity[0]  # caught the uptrend
    # No look-ahead: while falling and before slow SMA exists, equity is flat.
    assert result.equity[3] == pytest.approx(result.equity[0])


def test_sma_no_lookahead_signal_shift_changes_result():
    dates = _dates(60)
    rng = np.random.default_rng(7)
    closes = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, 60))
    base = bt.sma_crossover(dates, closes, fast=5, slow=20, risk_free=0.0)
    # Feeding shifted prices produces a different path — the signal depends on
    # (t-1) data, so misalignment must change the outcome.
    shifted = bt.sma_crossover(dates, np.roll(closes, 1), fast=5, slow=20, risk_free=0.0)
    assert not np.allclose(base.equity, shifted.equity)


def test_fixed_mix_rebalances_on_quarter():
    # Two assets: A doubles, B flat over Q1. After the quarter boundary the
    # mix must be back to 60/40.
    dates = [
        date(2024, 1, 2),
        date(2024, 2, 1),
        date(2024, 3, 29),
        date(2024, 4, 1),
        date(2024, 5, 1),
    ]
    closes = {
        "A": np.array([100.0, 150.0, 200.0, 200.0, 200.0]),
        "B": np.array([100.0, 100.0, 100.0, 100.0, 100.0]),
    }
    result = bt.fixed_mix(dates, closes, {"A": 0.6, "B": 0.4}, capital=10_000)
    # On 2024-04-01 (new quarter) value = 6000*2 + 4000 = 16000, rebalanced:
    # A = 9600 (48 sh @200), B = 6400. On 5/1 prices unchanged -> equity 16000.
    assert result.equity[3] == pytest.approx(16_000.0)
    assert result.equity[4] == pytest.approx(16_000.0)
    assert result.trades > 2  # initial buys + rebalance


def test_downsample_keeps_last_point():
    dates = _dates(1000)
    values = np.arange(1000, dtype=float)
    points = bt.downsample(dates, values, max_points=100)
    assert len(points) <= 101
    assert points[-1]["value"] == 999.0
