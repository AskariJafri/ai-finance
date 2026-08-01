from __future__ import annotations

import numpy as np
import pytest

from app.analytics.montecarlo import project


def _log_returns(n: int = 500, mu: float = 0.0003, sigma: float = 0.01) -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.normal(mu, sigma, n)


def test_seed_determinism():
    lr = _log_returns()
    a = project(lr, 10_000, horizon_years=1, method="gbm", n_paths=500, seed=1)
    b = project(lr, 10_000, horizon_years=1, method="gbm", n_paths=500, seed=1)
    assert a.bands == b.bands
    assert a.prob_loss == b.prob_loss


def test_band_monotonicity_every_month():
    lr = _log_returns()
    for method in ("gbm", "bootstrap"):
        result = project(lr, 10_000, horizon_years=5, method=method, n_paths=500, seed=3)
        for i in range(len(result.months)):
            assert (
                result.bands["p5"][i]
                <= result.bands["p25"][i]
                <= result.bands["p50"][i]
                <= result.bands["p75"][i]
                <= result.bands["p95"][i]
            )


def test_gbm_median_near_exponential_growth():
    lr = _log_returns(1000, mu=0.0004, sigma=0.008)
    result = project(lr, 10_000, horizon_years=5, method="gbm", n_paths=4000, seed=9)
    mu_hat = float(np.mean(lr))
    n_days = 5 * 12 * 21
    expected_median = 10_000 * np.exp(mu_hat * n_days)
    assert result.bands["p50"][-1] == pytest.approx(expected_median, rel=0.05)


def test_bootstrap_of_constant_returns_is_exact():
    lr = np.full(300, 0.001)  # every day identical -> resampling changes nothing
    result = project(lr, 10_000, horizon_years=1, method="bootstrap", n_paths=50, seed=5)
    expected = 10_000 * np.exp(0.001 * 12 * 21)
    # Bands are rounded to cents in the API payload.
    assert result.bands["p5"][-1] == pytest.approx(expected, abs=0.01)
    assert result.bands["p95"][-1] == pytest.approx(expected, abs=0.01)
    assert result.prob_loss == 0.0


def test_contributions_increase_final_values():
    lr = _log_returns()
    without = project(lr, 10_000, horizon_years=1, method="gbm", n_paths=500, seed=2)
    with_c = project(
        lr,
        10_000,
        horizon_years=1,
        method="gbm",
        n_paths=500,
        seed=2,
        monthly_contribution=500.0,
    )
    assert with_c.bands["p50"][-1] > without.bands["p50"][-1] + 500 * 11
