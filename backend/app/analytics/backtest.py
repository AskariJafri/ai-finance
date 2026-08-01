"""Strategy backtests on daily adjusted closes — deterministic, explainable.

Inputs are date-aligned close arrays (from invest_math.align_series). Because
closes are dividend/split adjusted, results approximate TOTAL return
(dividends reinvested). No transaction costs or taxes are modeled; the
explainers say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type

import numpy as np

from app.analytics.invest_math import (
    TRADING_DAYS,
    annualized_vol,
    cagr,
    max_drawdown,
    money_weighted_irr,
    returns_from_closes,
    sharpe,
)


@dataclass(frozen=True)
class BacktestResult:
    dates: list[date_type]
    equity: np.ndarray  # portfolio value per day, starts at initial capital
    trades: int
    contributed: float | None = None  # DCA only


def _metrics(result: BacktestResult, risk_free: float) -> dict:
    r = returns_from_closes(result.equity)
    years = len(result.equity) / TRADING_DAYS
    return {
        "cagr": cagr(float(result.equity[0]), float(result.equity[-1]), years),
        "ann_vol": annualized_vol(r),
        "sharpe": sharpe(r, risk_free),
        "max_drawdown": max_drawdown(r),
        "trades": result.trades,
    }


def summarize(result: BacktestResult, risk_free: float) -> dict:
    return _metrics(result, risk_free)


def buy_hold(
    dates: list[date_type],
    closes: dict[str, np.ndarray],
    weights: dict[str, float],
    capital: float = 10_000.0,
) -> BacktestResult:
    """Buy at the first close with fixed weights, never touch it again."""
    shares = {s: capital * w / closes[s][0] for s, w in weights.items()}
    equity = np.zeros(len(dates))
    for s, n in shares.items():
        equity += n * closes[s]
    return BacktestResult(dates=dates, equity=equity, trades=len(weights))


def dca(
    dates: list[date_type],
    closes: dict[str, np.ndarray],
    weights: dict[str, float],
    monthly_amount: float,
) -> BacktestResult:
    """Buy amount*w_i of each symbol at the first trading day of each month.

    Fractional shares; adjusted closes mean dividends are implicitly
    reinvested. Also tracks cumulative contributions for the value-vs-cost
    chart and the money-weighted return.
    """
    shares = {s: 0.0 for s in weights}
    equity = np.zeros(len(dates))
    contributed = 0.0
    contributions: list[tuple[float, int]] = []  # (amount, day index)
    trades = 0
    prev_month: tuple[int, int] | None = None
    for i, d in enumerate(dates):
        month = (d.year, d.month)
        if month != prev_month:
            prev_month = month
            for s, w in weights.items():
                buy = monthly_amount * w
                shares[s] += buy / closes[s][i]
            contributed += monthly_amount
            contributions.append((monthly_amount, i))
            trades += len(weights)
        equity[i] = sum(shares[s] * closes[s][i] for s in weights)
    return BacktestResult(dates=dates, equity=equity, trades=trades, contributed=contributed)


def dca_irr(result: BacktestResult) -> float | None:
    """Money-weighted annual return of a DCA result."""
    if result.contributed is None:
        return None
    n = len(result.dates)
    # Reconstruct contribution timing: first trading day of each month.
    contribs: list[tuple[float, float]] = []
    prev_month: tuple[int, int] | None = None
    per_month = result.contributed / max(1, len({(d.year, d.month) for d in result.dates}))
    for i, d in enumerate(result.dates):
        month = (d.year, d.month)
        if month != prev_month:
            prev_month = month
            years_invested = (n - 1 - i) / TRADING_DAYS
            contribs.append((per_month, years_invested))
    return money_weighted_irr(contribs, float(result.equity[-1]))


def sma_crossover(
    dates: list[date_type],
    closes: np.ndarray,
    fast: int = 50,
    slow: int = 200,
    capital: float = 10_000.0,
    risk_free: float = 0.04,
) -> BacktestResult:
    """Long when SMA_fast(t-1) > SMA_slow(t-1), else cash earning rf.

    The signal uses YESTERDAY's SMAs and trades at today's close — no
    look-ahead. Days before the slow SMA exists are spent in cash.
    """
    n = len(closes)
    rf_d = risk_free / TRADING_DAYS

    def sma(window: int) -> np.ndarray:
        out = np.full(n, np.nan)
        if n >= window:
            cs = np.cumsum(np.insert(closes, 0, 0.0))
            out[window - 1 :] = (cs[window:] - cs[:-window]) / window
        return out

    fast_sma, slow_sma = sma(fast), sma(slow)
    equity = np.zeros(n)
    equity[0] = capital
    in_market = False
    trades = 0
    r = returns_from_closes(closes)
    for i in range(1, n):
        signal_idx = i - 1
        want_long = (
            not np.isnan(slow_sma[signal_idx]) and fast_sma[signal_idx] > slow_sma[signal_idx]
        )
        if want_long != in_market:
            in_market = want_long
            trades += 1
        daily = r[i - 1] if in_market else rf_d
        equity[i] = equity[i - 1] * (1.0 + daily)
    return BacktestResult(dates=dates, equity=equity, trades=trades)


def fixed_mix(
    dates: list[date_type],
    closes: dict[str, np.ndarray],
    weights: dict[str, float],
    capital: float = 10_000.0,
    rebalance: str = "quarterly",
) -> BacktestResult:
    """Hold fixed weights (e.g. 60/40), rebalancing back to target on the
    first trading day of each quarter (or month)."""
    shares = {s: capital * w / closes[s][0] for s, w in weights.items()}
    equity = np.zeros(len(dates))
    trades = len(weights)
    prev_period: tuple | None = None

    def period_of(d: date_type) -> tuple:
        if rebalance == "monthly":
            return (d.year, d.month)
        return (d.year, (d.month - 1) // 3)

    for i, d in enumerate(dates):
        value = sum(shares[s] * closes[s][i] for s in weights)
        period = period_of(d)
        if prev_period is None:
            prev_period = period
        elif period != prev_period:
            prev_period = period
            for s, w in weights.items():
                shares[s] = value * w / closes[s][i]
            trades += len(weights)
        equity[i] = value
    return BacktestResult(dates=dates, equity=equity, trades=trades)


def downsample(dates: list[date_type], values: np.ndarray, max_points: int = 250) -> list[dict]:
    """Thin an equity curve for mobile rendering, always keeping the last point."""
    n = len(dates)
    if n <= max_points:
        idx = range(n)
    else:
        step = n / max_points
        idx = sorted({min(int(i * step), n - 1) for i in range(max_points)} | {n - 1})
    return [{"date": dates[i].isoformat(), "value": round(float(values[i]), 2)} for i in idx]
