"""Technical indicators — pure functions, no I/O."""

from __future__ import annotations

import pytest

from app.analytics.indicators import compute_indicators


def test_uptrend_and_momentum():
    closes = [100 + i for i in range(60)]  # strictly rising
    ind = compute_indicators(closes)
    assert ind.trend == "up"
    assert ind.rsi14 == 100.0  # only gains
    assert ind.ret_1m is not None and ind.ret_1m > 0
    assert ind.max_drawdown == 0.0  # never fell


def test_downtrend():
    closes = [200 - i for i in range(60)]  # strictly falling
    ind = compute_indicators(closes)
    assert ind.trend == "down"
    assert ind.rsi14 == 0.0
    assert ind.max_drawdown is not None and ind.max_drawdown < 0


def test_requires_data():
    with pytest.raises(ValueError):
        compute_indicators([])
