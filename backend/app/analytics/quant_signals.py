"""The house signal: a composite of published, out-of-sample-tested price
anomalies, computed from completed daily closes.

This replaced an SMA20/SMA50 crossover plus RSI. That rule had two problems a
trader would spot at once. It was *fast* — a 20/50 cross flips on a couple of
weeks of noise, so the verdict on a holding changed from one day to the next.
And it sold strength: "RSI >= 80 → exit" fights the single most robust
finding in the price-signal literature, which is that winners keep winning
over 3–12 months. Overbought is not a sell signal for a momentum stock; it is
what a momentum stock looks like.

Every component below is a published result that has held up out of sample
and across markets. Each is mapped to -1..+1 by scaling with the asset's own
volatility first, so a 10% move means more for a utility than for a biotech —
the same normalisation the trend-following literature uses.

  ===================  ======  ===============================================
  component            weight  source
  ===================  ======  ===============================================
  ``tsmom``            0.30    Time-series momentum: the sign and size of the
                               3/6/12-month return, each in units of its own
                               sigma. Moskowitz, Ooi & Pedersen (2012);
                               Hurst, Ooi & Pedersen, "A Century of Evidence
                               on Trend-Following" (2017).
  ``mom_12_1``         0.25    12-month return skipping the latest month,
                               risk-adjusted. Jegadeesh & Titman (1993); the
                               skip month avoids short-term reversal; dividing
                               by volatility is Barroso & Santa-Clara (2015).
  ``trend200``         0.15    Price vs its 200-day average — the 10-month
                               timing rule. Faber, "A Quantitative Approach to
                               Tactical Asset Allocation" (2007).
  ``high52``           0.15    Nearness to the 52-week high. George & Hwang
                               (2004): explains most of momentum's profit and
                               does not reverse.
  ``low_vol``          0.10    Lower volatility, higher risk-adjusted return.
                               Ang, Hodrick, Xing & Zhang (2006); Frazzini &
                               Pedersen, "Betting Against Beta" (2014).
  ``reversal``         0.05    Last month's return, inverted: one-month
                               winners give some back. Jegadeesh (1990);
                               Lehmann (1990). Small, and used mainly so a
                               name that just spiked is not bought at the top.
  ===================  ======  ===============================================

Decisions use a **no-trade band** (hysteresis): the score to open or add is
higher than the score that would trim. Something between the two is held,
whatever the day-to-day wobble — the core of every practical rebalancing
policy (Garleanu & Pedersen 2013; Vanguard's threshold rebalancing research),
and the direct fix for verdicts that change every time you ask.

Exits on a broken trend are a **trailing stop**: a drawdown from the 52-week
high of roughly 2.5 quarterly standard deviations, confirmed by the price being
under its 200-day average. Kaminski & Lo (2014) show stop-losses add value
exactly when returns are trending, and Han, Zhou & Zhu (2016) that a stop
roughly halves momentum's crash risk. The 200-day confirmation keeps it from
selling an ordinary pull-back inside an uptrend.

Pure functions over floats — no I/O — so all of it is unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

TRADING_DAYS = 252

#: Component weights. They sum to 1; when a component cannot be computed for
#: lack of history the rest are renormalised rather than the score shrunk, so a
#: young listing is judged on what can be measured instead of looking neutral.
WEIGHTS: dict[str, float] = {
    "tsmom": 0.30,
    "mom_12_1": 0.25,
    "trend200": 0.15,
    "high52": 0.15,
    "low_vol": 0.10,
    "reversal": 0.05,
}

#: Below this many closes the signal is not computed at all. Sixty sessions is
#: one usable horizon (3 months) plus a volatility estimate; anything shorter is
#: too little to tell trend from noise.
MIN_BARS = 64

# --- the no-trade band -----------------------------------------------------
#: Open a new position at or above this score…
BUY_ENTRY = 0.25
#: …add to one you hold at or above this (a higher bar: it concentrates)…
ADD_ENTRY = 0.35
#: …and trim at or below this. Everything between is HOLD.
TRIM_EXIT = -0.25
#: A full exit rather than a partial trim.
EXIT_SCORE = -0.55
#: How close to a fresh entry a score must be before the committee may promote
#: a SKIP to a BUY. Below it, the trend is not there to be confirmed.
NEAR_BUY = 0.10
#: A one-month move this many sigmas up, and at least CHASE_MIN_MOVE in
#: absolute terms, is chasing: wait for it to settle before opening or adding.
#: Both legs, so a quiet fund drifting up is not mistaken for a spike.
CHASE_SIGMAS = 3.0
CHASE_MIN_MOVE = 0.15

# --- trailing stop ---------------------------------------------------------
STOP_SIGMAS_QUARTER = 2.5
STOP_FLOOR = 0.15
STOP_CAP = 0.40

#: Diversified index funds are not traded like single names. A market-cap
#: index has no single-company failure mode to stop out of, and selling one in
#: a drawdown is the classic retail mistake — so no trailing stop applies, and
#: a trim needs a deeply negative trend (the Faber rule, not a wobble).
BROAD_ETFS = frozenset(
    {
        # US broad market / S&P 500 / Nasdaq-100 / world
        "SPY",
        "VOO",
        "IVV",
        "VTI",
        "ITOT",
        "SCHB",
        "SPLG",
        "QQQ",
        "QQQM",
        "VT",
        "VXUS",
        "IXUS",
        "ACWI",
        "VEA",
        "IEFA",
        "VWO",
        "IEMG",
        "EFA",
        "DIA",
        "RSP",
        "SCHX",
        # Canadian-listed equity index funds and asset-allocation ETFs
        "VFV.TO",
        "ZSP.TO",
        "XUS.TO",
        "XSP.TO",
        "HXS.TO",
        "VSP.TO",
        "XUU.TO",
        "VUN.TO",
        "XQQ.TO",
        "ZQQ.TO",
        "HXQ.TO",
        "QQC.TO",
        "XIU.TO",
        "XIC.TO",
        "VCN.TO",
        "ZCN.TO",
        "HXT.TO",
        "ZIU.TO",
        "XEF.TO",
        "VIU.TO",
        "ZEA.TO",
        "XEC.TO",
        "VEE.TO",
        "ZEM.TO",
        "XAW.TO",
        "VXC.TO",
        "XEQT.TO",
        "VEQT.TO",
        "ZEQT.TO",
        "HEQT.TO",
        "XGRO.TO",
        "VGRO.TO",
        "ZGRO.TO",
        "XBAL.TO",
        "VBAL.TO",
        "ZBAL.TO",
        "VCNS.TO",
        "XCNS.TO",
        "VRIF.TO",
        # Bond funds
        "BND",
        "AGG",
        "TLT",
        "IEF",
        "SHY",
        "LQD",
        "HYG",
        "BSV",
        "BIV",
        "BLV",
        "XBB.TO",
        "ZAG.TO",
        "VAB.TO",
        "VSB.TO",
        "ZFL.TO",
        "XSB.TO",
        "ZDB.TO",
    }
)
#: The deeper bar a broad fund's score must clear before a trim.
ETF_TRIM_EXIT = -0.50


def is_broad_fund(symbol: str | None) -> bool:
    return bool(symbol) and symbol.upper() in BROAD_ETFS


@dataclass(frozen=True)
class QuantSignal:
    score: float  # composite, -1..+1
    components: dict[str, float] = field(default_factory=dict)  # each -1..+1
    vol: float | None = None  # annualised volatility forecast
    sma200: float | None = None
    above_sma200: bool | None = None
    mom_12_1: float | None = None  # simple return, 12m skipping the last month
    gap_52w: float | None = None  # price / 52-week high - 1  (<= 0)
    stop_level: float | None = None  # the drawdown that trips the stop
    stop_hit: bool = False
    chasing: bool = False  # last month's move is > CHASE_SIGMAS
    bars: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    # --- decisions --------------------------------------------------------
    def decide(self, *, held: bool, symbol: str | None = None) -> str:
        """``BUY | SKIP`` for a candidate, ``ADD | HOLD | TRIM`` for a holding.

        The bands are what keep a verdict from flickering: a score has to
        travel from below TRIM_EXIT to above ADD_ENTRY to flip a holding
        from one side to the other.
        """
        fund = is_broad_fund(symbol)
        if not held:
            if self.score >= BUY_ENTRY and self.above_sma200 is not False and not self.chasing:
                return "BUY"
            return "SKIP"
        if self.stop_hit and not fund:
            return "TRIM"
        exit_at = ETF_TRIM_EXIT if fund else TRIM_EXIT
        if self.score <= exit_at and self.above_sma200 is not True:
            return "TRIM"
        if self.score >= ADD_ENTRY and self.above_sma200 is not False and not self.chasing:
            return "ADD"
        return "HOLD"

    def full_exit(self, symbol: str | None = None) -> bool:
        """Whether a TRIM should close the position rather than halve it."""
        if is_broad_fund(symbol):
            return False
        return self.stop_hit or self.score <= EXIT_SCORE

    def confidence(self, action: str) -> float:
        """How sure the signal is of ``action``, 0.5..0.9.

        Directional verdicts scale with the size of the score; a HOLD is most
        confident in the middle of the band and least at its edges.
        """
        s = abs(self.score)
        if action in ("BUY", "ADD", "TRIM"):
            conf = 0.55 + 0.4 * min(1.0, s / 0.8)
            if action == "TRIM" and self.stop_hit:
                conf = max(conf, 0.8)
            return round(min(conf, 0.9), 2)
        edge = ADD_ENTRY if self.score > 0 else abs(TRIM_EXIT)
        return round(0.5 + 0.25 * max(0.0, 1 - s / edge), 2)

    def line(self) -> str:
        """One human-readable line, used in reasons and in the committee prompt."""
        parts = [f"trend score {self.score:+.2f}"]
        if self.mom_12_1 is not None:
            parts.append(f"12-1m momentum {self.mom_12_1 * 100:+.0f}%")
        if self.above_sma200 is not None:
            parts.append("above 200-day avg" if self.above_sma200 else "below 200-day avg")
        if self.gap_52w is not None:
            parts.append(
                "at 52-wk high"
                if self.gap_52w > -0.005
                else f"{-self.gap_52w * 100:.0f}% below 52-wk high"
            )
        if self.vol is not None:
            parts.append(f"vol {self.vol * 100:.0f}%")
        if self.stop_hit:
            parts.append(f"trailing stop hit (>{(self.stop_level or 0) * 100:.0f}% off high)")
        if self.chasing:
            parts.append("spiked this month — waiting for it to settle")
        return ", ".join(parts)


# --- helpers ------------------------------------------------------------------
def _stdev(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def vol_forecast(log_rets: list[float]) -> float | None:
    """Annualised volatility: an even blend of the last quarter and the last
    year. The short window reacts to a regime change, the long one keeps a
    single wild week from halving a position — the same blend risk desks use
    in place of a single lookback (cf. RiskMetrics)."""
    if len(log_rets) < 20:
        return None
    short = _stdev(log_rets[-63:])
    long_ = _stdev(log_rets[-TRADING_DAYS:])
    vals = [v for v in (short, long_) if v]
    if not vals:
        return None
    daily = sum(vals) / len(vals)
    return max(daily * math.sqrt(TRADING_DAYS), 0.03)  # floor: never divide by ~0


def compute_signal(closes: list[float]) -> QuantSignal | None:
    """The composite signal for ascending daily closes, or None if too short.

    Pass *completed* sessions only. A bar for today while the market is open is
    a moving snapshot, and including it is what makes the same question give a
    different answer at 10am and at 2pm.
    """
    closes = [c for c in closes if c and c > 0]
    n = len(closes)
    if n < MIN_BARS:
        return None
    last = closes[-1]
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
    vol = vol_forecast(log_rets)
    if vol is None:
        return None

    comps: dict[str, float] = {}

    # Time-series momentum across 3/6/12 months, each in sigma units.
    horizons = [h for h in (63, 126, TRADING_DAYS) if n > h]
    if horizons:
        zs = []
        for h in horizons:
            r = math.log(last / closes[-h - 1])
            zs.append(math.tanh(r / (vol * math.sqrt(h / TRADING_DAYS))))
        comps["tsmom"] = sum(zs) / len(zs)

    # 12-1 momentum (6-1 when a year is not available), risk-adjusted.
    mom_12_1 = None
    if n > TRADING_DAYS:
        lookback = TRADING_DAYS
    elif n > 126:
        lookback = 126
    else:
        lookback = 0
    if lookback:
        start, end = closes[-lookback - 1], closes[-22]
        mom_12_1 = end / start - 1
        span = (lookback - 21) / TRADING_DAYS
        comps["mom_12_1"] = math.tanh(math.log(end / start) / (vol * math.sqrt(span)))

    # Faber's 10-month rule: distance from the 200-day average, in vol units.
    sma200 = None
    above = None
    if n >= 200:
        sma200 = sum(closes[-200:]) / 200
        above = last > sma200
        comps["trend200"] = math.tanh(3 * math.log(last / sma200) / vol)

    # George & Hwang: price relative to the 52-week (or available) high.
    window = closes[-TRADING_DAYS:]
    high = max(window)
    gap = last / high - 1
    comps["high52"] = _clip(1 + 2 * gap / vol)

    # Low-volatility anomaly.
    comps["low_vol"] = _clip((0.30 - vol) / 0.20)

    # Short-term reversal on the last month.
    chasing = False
    if n > 22:
        r1 = math.log(last / closes[-22])
        z1 = r1 / (vol * math.sqrt(21 / TRADING_DAYS))
        comps["reversal"] = -math.tanh(z1 / 2)
        chasing = z1 > CHASE_SIGMAS and (last / closes[-22] - 1) > CHASE_MIN_MOVE

    total_w = sum(WEIGHTS[k] for k in comps)
    score = sum(WEIGHTS[k] * v for k, v in comps.items()) / total_w if total_w else 0.0

    stop_level = _clip(STOP_SIGMAS_QUARTER * vol * math.sqrt(0.25), STOP_FLOOR, STOP_CAP)
    # Both legs, so an ordinary pull-back inside a long uptrend is not sold:
    # deep off the high AND under the long average AND a negative composite.
    stop_hit = gap <= -stop_level and above is False and score < 0

    return QuantSignal(
        score=round(score, 4),
        components={k: round(v, 4) for k, v in comps.items()},
        vol=round(vol, 4),
        sma200=round(sma200, 4) if sma200 is not None else None,
        above_sma200=above,
        mom_12_1=round(mom_12_1, 4) if mom_12_1 is not None else None,
        gap_52w=round(gap, 4),
        stop_level=round(stop_level, 4),
        stop_hit=stop_hit,
        chasing=chasing,
        bars=n,
    )


# --- sizing -------------------------------------------------------------------
#: The standalone annual volatility one position may contribute, as a share of
#: the account. Position weight is capped at this divided by the asset's vol:
#: 3.5% / 20% vol = 17.5% of the account; 3.5% / 60% vol = ~6%. Inverse-vol
#: sizing — equal *risk* rather than equal dollars — is the core of volatility
#: targeting (Moreira & Muir 2017) and risk parity.
POSITION_RISK_BUDGET = 0.035


def vol_capped_weight(vol: float | None, cap: float, symbol: str | None = None) -> float:
    """The most of the account a position should be, given its volatility."""
    if vol is None or vol <= 0 or is_broad_fund(symbol):
        return cap
    return min(cap, POSITION_RISK_BUDGET / vol)


def alpha_weight(score: float, vol: float | None) -> float:
    """Relative size of a buy among several.

    Grinold's rule: expected alpha = IC x volatility x score, and the
    mean-variance optimal weight is alpha / (risk aversion x variance), so the
    weight is proportional to score / volatility. Floors keep a barely-positive
    score from rounding to nothing and a missing vol from dividing by zero.
    """
    return max(score, 0.05) / max(vol or 0.25, 0.08)


# --- reconciling the committee with the signal -------------------------------
_HELD_LADDER = {"TRIM": -1, "HOLD": 0, "ADD": 1}


def reconcile(
    quant_action: str,
    ai_action: str,
    signal: QuantSignal,
    *,
    held: bool,
    symbol: str | None = None,
) -> tuple[str, str | None]:
    """Combine the signal's verdict with the committee's. Returns
    ``(final_action, why_overruled)``; the second is None when the committee's
    answer stands.

    The signal is the anchor; the committee is a discretionary overlay with
    bounded authority — the way a systematic desk lets a PM override a model:
    one notch, never against a hard risk rule. That bound is what makes the
    plan stable. The model's answer to the same question varies from call to
    call, and from one fallback model to the next; capped at one step from a
    deterministic signal, that variance can no longer turn an ADD into an EXIT.
    """
    ai_action = ai_action.upper()
    if held:
        if signal.stop_hit and not is_broad_fund(symbol) and quant_action == "TRIM":
            if ai_action != "TRIM":
                return "TRIM", "the trailing stop is a hard risk rule, not a judgment call"
            return "TRIM", None
        q = _HELD_LADDER.get(quant_action, 0)
        a = _HELD_LADDER.get(ai_action, 0)
        if abs(a - q) <= 1:
            # A one-step move — but not a sale of a strong uptrend, and not
            # more money into one that is broken.
            if ai_action == "TRIM" and signal.score >= ADD_ENTRY:
                return (
                    "HOLD",
                    "the trend is strongly positive; one view is not enough to sell into it",
                )
            if ai_action == "ADD" and signal.above_sma200 is False:
                return (
                    "HOLD",
                    "the price is below its 200-day average — no adding to a broken trend",
                )
            return ai_action, None
        final = {-1: "TRIM", 0: "HOLD", 1: "ADD"}[q + (1 if a > q else -1)]
        return final, "the committee can move the signal's verdict one step, not two"

    # Candidates: the committee may always veto a buy; it may only promote a
    # skip when the trend is nearly there and intact.
    if ai_action == "BUY" and quant_action != "BUY":
        if signal.score >= NEAR_BUY and signal.above_sma200 is not False and not signal.chasing:
            return "BUY", None
        return "SKIP", "the price trend doesn't support opening it yet"
    if ai_action == "HOLD":
        return "SKIP", None
    return ai_action if ai_action in ("BUY", "SKIP") else quant_action, None
