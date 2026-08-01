"""Monte Carlo portfolio projections — honest probabilistic bands, never a
single "predicted price".

Two methods, both parameterized ONLY from the portfolio's own history:
- GBM: daily log returns drawn from N(mu_hat, sigma_hat) fitted to history.
- Bootstrap: daily log returns resampled (with replacement) from the realized
  series — preserves fat tails and the correlation structure baked into the
  realized portfolio returns.

Seeded via np.random.default_rng so results are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TRADING_DAYS = 252
DAYS_PER_MONTH = 21


@dataclass(frozen=True)
class ProjectionResult:
    months: list[int]  # 1..horizon_months
    bands: dict[str, list[float]]  # p5/p25/p50/p75/p95 -> value per month
    prob_loss: float  # P(final value < contributions + start)
    mu_ann: float
    sigma_ann: float


def project(
    log_returns: np.ndarray,
    start_value: float,
    *,
    horizon_years: int,
    method: str = "gbm",
    n_paths: int = 2000,
    monthly_contribution: float = 0.0,
    seed: int | None = None,
) -> ProjectionResult:
    rng = np.random.default_rng(seed)
    mu_hat = float(np.mean(log_returns))
    sigma_hat = float(np.std(log_returns, ddof=1))
    horizon_months = horizon_years * 12
    n_days = horizon_months * DAYS_PER_MONTH

    if method == "bootstrap":
        daily = rng.choice(log_returns, size=(n_paths, n_days), replace=True)
    else:  # gbm
        daily = rng.normal(mu_hat, sigma_hat, size=(n_paths, n_days))

    growth = np.exp(daily)
    values = np.full(n_paths, start_value)
    monthly_values = np.empty((n_paths, horizon_months))
    for m in range(horizon_months):
        chunk = growth[:, m * DAYS_PER_MONTH : (m + 1) * DAYS_PER_MONTH]
        values = values * np.prod(chunk, axis=1)
        if monthly_contribution:
            values = values + monthly_contribution
        monthly_values[:, m] = values

    percentiles = np.percentile(monthly_values, [5, 25, 50, 75, 95], axis=0)
    total_in = start_value + monthly_contribution * horizon_months
    return ProjectionResult(
        months=list(range(1, horizon_months + 1)),
        bands={
            "p5": [round(float(v), 2) for v in percentiles[0]],
            "p25": [round(float(v), 2) for v in percentiles[1]],
            "p50": [round(float(v), 2) for v in percentiles[2]],
            "p75": [round(float(v), 2) for v in percentiles[3]],
            "p95": [round(float(v), 2) for v in percentiles[4]],
        },
        prob_loss=round(float(np.mean(monthly_values[:, -1] < total_in)), 4),
        mu_ann=round(mu_hat * TRADING_DAYS, 6),
        sigma_ann=round(sigma_hat * np.sqrt(TRADING_DAYS), 6),
    )
