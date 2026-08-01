"""Portfolio risk metrics — pure numpy, no I/O, unit-tested against hand values.

Conventions (documented in the explainers the API serves):
- A = 252 trading days/year; ddof=1 for all sample statistics.
- Simple daily returns r_t = P_t/P_{t-1} - 1 for aggregation and drawdown;
  log returns for GBM parameterization (see montecarlo.py).
- Portfolio daily return = sum_i w_i * r_i,t with CURRENT weights held fixed
  across the window — an approximation (real weights drift); stated in docs.
- Metrics need >= MIN_DAYS overlapping trading days or they are None.
"""

from __future__ import annotations

from datetime import date as date_type

import numpy as np

TRADING_DAYS = 252
MIN_DAYS = 60


def align_series(
    series: dict[str, list[tuple[date_type, float]]],
) -> tuple[list[date_type], dict[str, np.ndarray]]:
    """Intersect all symbols' (date, close) series on common dates.

    Returns (sorted common dates, symbol -> closes array aligned to them).
    """
    if not series:
        return [], {}
    common: set[date_type] | None = None
    for points in series.values():
        dates = {d for d, _ in points}
        common = dates if common is None else common & dates
    dates_sorted = sorted(common or set())
    index = {d: i for i, d in enumerate(dates_sorted)}
    out: dict[str, np.ndarray] = {}
    for symbol, points in series.items():
        arr = np.empty(len(dates_sorted))
        for d, close in points:
            i = index.get(d)
            if i is not None:
                arr[i] = close
        out[symbol] = arr
    return dates_sorted, out


def returns_from_closes(closes: np.ndarray) -> np.ndarray:
    """Simple daily returns; length = len(closes) - 1."""
    return closes[1:] / closes[:-1] - 1.0


def log_returns_from_closes(closes: np.ndarray) -> np.ndarray:
    return np.log(closes[1:] / closes[:-1])


def portfolio_returns(weights: dict[str, float], returns: dict[str, np.ndarray]) -> np.ndarray:
    """r_p,t = sum_i w_i r_i,t (current-weight approximation)."""
    symbols = list(weights)
    matrix = np.vstack([returns[s] for s in symbols])
    w = np.array([weights[s] for s in symbols])
    return w @ matrix


def annualized_vol(r: np.ndarray) -> float | None:
    if len(r) < MIN_DAYS:
        return None
    sd = float(np.std(r, ddof=1))
    return sd * np.sqrt(TRADING_DAYS)


def sharpe(r: np.ndarray, risk_free: float) -> float | None:
    vol = annualized_vol(r)
    if vol is None or vol == 0:
        return None
    return (float(np.mean(r)) * TRADING_DAYS - risk_free) / vol


def sortino(r: np.ndarray, risk_free: float) -> float | None:
    """Downside deviation over ALL days (zeros included for up days)."""
    if len(r) < MIN_DAYS:
        return None
    rf_d = risk_free / TRADING_DAYS
    downside = np.minimum(r - rf_d, 0.0)
    dd = float(np.sqrt(TRADING_DAYS * np.mean(downside**2)))
    if dd == 0:
        return None
    return (float(np.mean(r)) * TRADING_DAYS - risk_free) / dd


def max_drawdown(r: np.ndarray) -> float | None:
    """Most negative peak-to-trough decline of the cumulative equity curve."""
    if len(r) == 0:
        return None
    equity = np.cumprod(1.0 + r)
    running_max = np.maximum.accumulate(equity)
    return float(np.min(equity / running_max - 1.0))


def beta(r: np.ndarray, benchmark: np.ndarray) -> float | None:
    if len(r) < MIN_DAYS or len(r) != len(benchmark):
        return None
    var_b = float(np.var(benchmark, ddof=1))
    if var_b == 0:
        return None
    cov = float(np.cov(r, benchmark, ddof=1)[0, 1])
    return cov / var_b


def hist_var_cvar(r: np.ndarray, confidence: float = 0.95) -> tuple[float, float] | None:
    """Historical one-day (VaR, CVaR) as POSITIVE loss fractions.

    VaR = -quantile(r, 1 - confidence); CVaR = -mean of returns at or below
    that quantile (the average of the worst tail).
    """
    if len(r) < MIN_DAYS:
        return None
    q = float(np.quantile(r, 1.0 - confidence))
    tail = r[r <= q]
    if len(tail) == 0:
        return None
    return -q, -float(np.mean(tail))


def hhi(weights: list[float]) -> float:
    """Herfindahl-Hirschman concentration index: sum of squared weights."""
    return float(sum(w * w for w in weights))


def correlation_matrix(returns: dict[str, np.ndarray]) -> tuple[list[str], list[list[float]]]:
    symbols = sorted(returns)
    if len(symbols) < 2:
        return symbols, [[1.0]] if symbols else []
    matrix = np.corrcoef(np.vstack([returns[s] for s in symbols]))
    return symbols, [[round(float(v), 4) for v in row] for row in matrix]


def cagr(start_value: float, end_value: float, years: float) -> float | None:
    if start_value <= 0 or years <= 0:
        return None
    return (end_value / start_value) ** (1.0 / years) - 1.0


def money_weighted_irr(
    contributions: list[tuple[float, float]], final_value: float
) -> float | None:
    """Annualized IRR via bisection: solve sum c_j (1+irr)^{t_j} = FV,
    where each contribution is (amount, years it stayed invested)."""
    if not contributions or final_value <= 0:
        return None

    def fv(rate: float) -> float:
        return sum(c * (1.0 + rate) ** t for c, t in contributions)

    lo, hi = -0.99, 10.0
    if (fv(lo) - final_value) * (fv(hi) - final_value) > 0:
        return None  # no sign change: IRR outside a sane range
    for _ in range(100):
        mid = (lo + hi) / 2
        if (fv(mid) - final_value) * (fv(lo) - final_value) <= 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 6)
