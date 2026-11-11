"""Pure technical indicators computed from a daily close series.

Fed to the AI committee so its agents reason over real numbers (the LLM never
invents figures — it only interprets these). All inputs are plain floats; no
provider or DB access here, so this stays trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.analytics.quant_signals import QuantSignal, compute_signal


@dataclass(frozen=True)
class Indicators:
    last: float
    sma20: float | None
    sma50: float | None
    rsi14: float | None
    ret_1m: float | None  # ~21 trading days
    ret_3m: float | None  # ~63
    ret_6m: float | None  # ~126
    max_drawdown: float | None
    vol_annual: float | None
    trend: str  # "up" | "down" | "flat"
    # The composite house signal (see quant_signals). None below ~3 months of
    # history, in which case the rules fall back to the short-window reading.
    signal: QuantSignal | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def _ret(closes: list[float], n: int) -> float | None:
    if len(closes) <= n or closes[-n - 1] == 0:
        return None
    return round(closes[-1] / closes[-n - 1] - 1, 4)


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def _max_drawdown(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    peak = closes[0]
    worst = 0.0
    for c in closes:
        peak = max(peak, c)
        if peak:
            worst = min(worst, c / peak - 1)
    return round(worst, 4)


def _vol_annual(closes: list[float]) -> float | None:
    if len(closes) < 20:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round((var**0.5) * (252**0.5), 4)


def compute_indicators(closes: list[float]) -> Indicators:
    """closes = ascending daily closes (oldest first)."""
    closes = [c for c in closes if c and c > 0]
    if not closes:
        raise ValueError("no price history")
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    trend = "flat"
    if sma20 is not None and sma50 is not None:
        if sma20 > sma50 * 1.005:
            trend = "up"
        elif sma20 < sma50 * 0.995:
            trend = "down"
    return Indicators(
        last=round(closes[-1], 4),
        sma20=round(sma20, 4) if sma20 else None,
        sma50=round(sma50, 4) if sma50 else None,
        rsi14=_rsi(closes),
        ret_1m=_ret(closes, 21),
        ret_3m=_ret(closes, 63),
        ret_6m=_ret(closes, 126),
        max_drawdown=_max_drawdown(closes),
        vol_annual=_vol_annual(closes),
        trend=trend,
        signal=compute_signal(closes),
    )
