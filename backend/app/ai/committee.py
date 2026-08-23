"""AI Investment Committee — several role-playing agents reason over the SAME
real numbers (a live quote + technical indicators) and a CIO synthesizes a
verdict: BUY / SKIP / HOLD.

Pass ``held`` and the question changes to TRIM / HOLD / ADD. That is not
cosmetic: "I would not open this today" is true of most things most of the time,
so reading a SKIP as a sell recommendation turns the committee into a machine
that tells you to liquidate. An owned position is judged on whether to keep it,
with its size and unrealized return in front of the seats and an explicit
instruction that selling clears a higher bar than declining to buy.

Design rules mirrored from the rest of the app:
- The LLM never invents figures — every number in the prompts comes from
  `Indicators` / the quote. Agents only *interpret*.
- Provider-agnostic: takes an `LLMProvider`, so the hosted model behind it is an
  env change, never a code change.
- Never fails the request: imperfect JSON is salvaged, a short roster is padded,
  and if the LLM is unavailable the verdict falls back to a transparent
  rules-based read of the indicators.
- Educational only — not investment advice. Paper/sandbox context.

**One call per ticker.** The five agents and the CIO used to be six separate
requests. Free hosted tiers meter requests per day, and Deploy-Cash runs the
committee over a whole shortlist, so the entire deliberation is now a single
request returning every take plus the verdict. A capable hosted model holds all
six roles in one pass; the six-call version existed because a 3B local model
could not.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from app.ai.llm.base import LLMProvider
from app.analytics.indicators import Indicators
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_ACTIONS = {"BUY", "SKIP", "HOLD"}

# A position you already own is a different question, and asking the entry
# question about it produces a systematic sell bias: "I wouldn't open this
# today" is true of most things most of the time, while "you should sell what
# you own" has to clear costs, taxes and the odds of being wrong. So a held
# position gets its own verdict vocabulary and its own instruction to weigh
# them.
VALID_HELD_ACTIONS = {"TRIM", "HOLD", "ADD"}


@dataclass(frozen=True)
class HeldContext:
    """What the seats need to know about a position that is already owned."""

    weight: float  # share of the portfolio, 0..1
    unrealized_pct: float  # 0.12 = up 12%


@dataclass(frozen=True)
class AgentTake:
    agent: str  # role key
    name: str  # display name
    stance: str  # bullish | bearish | neutral | caution
    confidence: float  # 0..1
    thesis: str


def _salvage_json(s: str) -> dict | None:
    start = s.find("{")
    if start == -1:
        return None
    s = s[start:]
    while s:
        cand = s.rstrip().rstrip(",")
        opens = cand.count("{") - cand.count("}")
        try:
            return json.loads(cand + "}" * max(0, opens))
        except json.JSONDecodeError:
            nl = s.rfind(",")
            if nl == -1:
                return None
            s = s[:nl]
    return None


def _parse_json(raw: str) -> dict:
    raw = re.sub(r"```(?:json)?|```", "", raw or "").strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    try:
        return json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        return _salvage_json(raw) or {}


def _clip(v, lo=0.0, hi=1.0, default=0.5) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _context(symbol: str, quote: dict, ind: Indicators) -> str:
    return (
        f"{symbol} @ {quote.get('price', '?')} {quote.get('currency', '')}. "
        f"Trend={ind.trend}. SMA20={ind.sma20}, SMA50={ind.sma50}. "
        f"RSI14={ind.rsi14}. "
        f"Returns 1m/3m/6m={_pct(ind.ret_1m)}/{_pct(ind.ret_3m)}/{_pct(ind.ret_6m)}. "
        f"Max drawdown={_pct(ind.max_drawdown)}. Annualized vol={_pct(ind.vol_annual)}."
    )


#: role key -> (display name, the seat's mandate). Order is the order the UI
#: renders takes in, and the order the model is asked to produce them.
ROLES: tuple[tuple[str, str, str], ...] = (
    (
        "research",
        "Research Analyst",
        "judges whether price trend and momentum justify owning this now; precise and skeptical",
    ),
    (
        "technical",
        "Technical Analyst",
        "reads momentum, trend and overbought/oversold from RSI and moving averages",
    ),
    (
        "risk",
        "Risk Officer",
        "protects capital above all else; weighs volatility and drawdown",
    ),
    (
        "bear",
        "Devil's Advocate",
        "attacks the bull case and names the failure scenarios",
    ),
    (
        "pm",
        "Portfolio Manager",
        "sizes the position; balances conviction against risk, small size when uncertain",
    ),
)

_STANCES = {"bullish", "bearish", "neutral", "caution"}

COMMITTEE_SYSTEM = (
    "You chair an investment committee and speak for every seat at the table. "
    "The seats are:\n"
    + "\n".join(f"- {key} ({name}): {mandate}" for key, name, mandate in ROLES)
    + "\nEach seat reaches its own honest conclusion from the SAME data — they must not "
    "converge out of politeness, and the Devil's Advocate must genuinely dissent when there "
    "is a case to make. Then, as Chief Investment Officer, weigh every seat and decide. "
    "Capital preservation beats returns; when uncertain, SKIP.\n"
    "Never invent, estimate or recompute a figure — interpret only the numbers given."
)

_COMMITTEE_JSON_RULE = (
    "Respond with ONLY a JSON object, no prose, no markdown:\n"
    '{"agents": ['
    '{"agent": "<' + "|".join(key for key, _, _ in ROLES) + '>", '
    '"stance": "<bullish|bearish|neutral|caution>", '
    '"confidence": <0..1>, "thesis": "<=40 words"}'
    "], "
    '"action": "<BUY|SKIP|HOLD>", "confidence": <0..1>, "rationale": "<=60 words"}\n'
    f"Include all {len(ROLES)} agents, once each, in the order listed above."
)

_HELD_JSON_RULE = _COMMITTEE_JSON_RULE.replace(
    '"action": "<BUY|SKIP|HOLD>"', '"action": "<TRIM|HOLD|ADD>"'
)

HELD_SYSTEM = (
    COMMITTEE_SYSTEM + "\n\nThis position is ALREADY OWNED. You are not deciding whether to open "
    "it — you are deciding what to do with it now.\n"
    "- TRIM: the case for reducing or exiting is clear and outweighs the cost of "
    "being wrong.\n"
    "- HOLD: keep it as it is. This is the right answer whenever the evidence is "
    "mixed — 'I would not buy more today' is a HOLD, not a TRIM.\n"
    "- ADD: the case for putting more money in is clear.\n"
    "Selling has a higher bar than declining to buy: it realizes the outcome, "
    "costs the spread, and forfeits the position if you are wrong. Default to "
    "HOLD when uncertain."
)


def _take(key: str, name: str, data: dict | None) -> AgentTake:
    data = data or {}
    stance = str(data.get("stance", "neutral")).lower()
    if stance not in _STANCES:
        stance = "neutral"
    thesis = str(data.get("thesis") or "No read available (model unavailable).").strip()
    return AgentTake(key, name, stance, round(_clip(data.get("confidence")), 2), thesis[:400])


def _takes_from(payload: dict) -> list[AgentTake]:
    """Map the model's agent list onto the fixed roster.

    Keyed by role rather than by position, and padded when a seat is missing, so
    a short or reordered reply still renders a full committee instead of failing
    the request.
    """
    raw = payload.get("agents")
    by_key: dict[str, dict] = {}
    if isinstance(raw, list):
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            key = str(item.get("agent", "")).lower().strip()
            if key not in {k for k, _, _ in ROLES} and i < len(ROLES):
                key = ROLES[i][0]  # unlabelled but in order
            if key:
                by_key.setdefault(key, item)
    return [_take(key, name, by_key.get(key)) for key, name, _ in ROLES]


async def run_committee(
    symbol: str,
    quote: dict,
    ind: Indicators,
    llm: LLMProvider,
    *,
    held: HeldContext | None = None,
) -> dict:
    """Deliberate on one ticker.

    Pass ``held`` when the user already owns it: the seats get the position's
    size and unrealized return, and the verdict comes back as TRIM/HOLD/ADD
    rather than BUY/SKIP/HOLD.
    """
    ctx = _context(symbol, quote, ind)
    if held is not None:
        ctx += (
            f" Currently held: {held.weight * 100:.1f}% of the portfolio, "
            f"unrealized {held.unrealized_pct * 100:+.1f}%."
        )
    prompt = (
        f"Deliberate on {symbol} @ {quote.get('price')} {quote.get('currency', '')}.\n"
        f"Data:\n{ctx}\n\n"
        "Give each seat's stance and one-line thesis, then your final decision.\n\n"
        f"{_HELD_JSON_RULE if held is not None else _COMMITTEE_JSON_RULE}"
    )

    try:
        raw = await llm.complete(
            prompt,
            system=HELD_SYSTEM if held is not None else COMMITTEE_SYSTEM,
            temperature=0.3,
            json_object=True,
        )
        payload = _parse_json(raw)
    except Exception as exc:  # noqa: BLE001 — any LLM error degrades to rules
        logger.warning("Committee failed for %s, using rules: %s", symbol, exc)
        payload = {}

    if not payload:
        # Nothing usable came back at all. Say so honestly rather than showing a
        # table of five seats that never spoke.
        return rules_only(symbol, quote, ind, held=held is not None)

    takes = _takes_from(payload)
    verdict = _verdict_from(payload, ind, takes, held=held is not None)
    return {
        "symbol": symbol,
        "price": quote.get("price"),
        "currency": quote.get("currency", "USD"),
        "action": verdict["action"],
        "confidence": verdict["confidence"],
        "summary": verdict["summary"],
        "source": verdict["source"],
        "agents": [t.__dict__ for t in takes],
        "indicators": ind.as_dict(),
    }


def _verdict_from(
    payload: dict, ind: Indicators, takes: list[AgentTake], *, held: bool = False
) -> dict:
    """The CIO's decision, or a transparent rules read when it is unusable."""
    action = str(payload.get("action", "")).upper()
    rationale = str(payload.get("rationale") or "").strip()
    if action in (VALID_HELD_ACTIONS if held else VALID_ACTIONS) and rationale:
        return {
            "action": action,
            "confidence": round(_clip(payload.get("confidence")), 2),
            "summary": rationale[:500],
            "source": "ai",
        }
    bullish = sum(1 for t in takes if t.stance == "bullish")
    bearish = sum(1 for t in takes if t.stance in ("bearish", "caution"))
    return _rules_verdict(ind, bullish, bearish, held=held)


async def run_committee_many(
    items: list[tuple[str, dict, Indicators]],
    llm: LLMProvider,
    *,
    use_ai: bool = True,
    held: dict[str, HeldContext] | None = None,
) -> list[dict]:
    """Deliberate over several tickers concurrently.

    Each ticker degrades on its own: a committee that errors falls back to
    ``rules_only`` for that symbol rather than losing the whole batch. One LLM
    call per ticker, and the provider caps how many are in flight at once — keep
    the list short anyway, since free tiers meter requests per day.
    """

    positions = held or {}

    async def one(symbol: str, quote: dict, ind: Indicators) -> dict:
        position = positions.get(symbol)
        if use_ai:
            try:
                return await run_committee(symbol, quote, ind, llm, held=position)
            except Exception as exc:  # noqa: BLE001 — degrade this symbol only
                logger.warning("Committee failed for %s, using rules: %s", symbol, exc)
        return rules_only(symbol, quote, ind, held=position is not None)

    return list(await asyncio.gather(*(one(s, q, i) for s, q, i in items)))


def rules_only(symbol: str, quote: dict, ind: Indicators, *, held: bool = False) -> dict:
    """Full committee result without any LLM call — used when AI is disabled or
    unreachable, so the feature still works (transparently rules-based)."""
    stance = "bullish" if ind.trend == "up" else "bearish" if ind.trend == "down" else "neutral"
    takes = [
        AgentTake(key, name, stance, 0.5, "AI offline — see the rules-based verdict below.")
        for key, name, _ in ROLES
    ]
    verdict = _rules_verdict(
        ind,
        1 if stance == "bullish" else 0,
        1 if stance == "bearish" else 0,
        held=held,
    )
    return {
        "symbol": symbol,
        "price": quote.get("price"),
        "currency": quote.get("currency", "USD"),
        "action": verdict["action"],
        "confidence": verdict["confidence"],
        "summary": verdict["summary"],
        "source": "rules",
        "agents": [t.__dict__ for t in takes],
        "indicators": ind.as_dict(),
    }


def _rules_verdict(ind: Indicators, bullish: int, bearish: int, *, held: bool = False) -> dict:
    """Transparent fallback when the LLM is unavailable — an honest read of the
    indicators, never a fabricated one."""
    reasons: list[str] = []
    score = 0
    if ind.trend == "up":
        score += 1
        reasons.append("uptrend (SMA20 > SMA50)")
    elif ind.trend == "down":
        score -= 1
        reasons.append("downtrend")
    if ind.rsi14 is not None:
        if ind.rsi14 >= 70:
            score -= 1
            reasons.append(f"overbought (RSI {ind.rsi14})")
        elif ind.rsi14 <= 30:
            score += 1
            reasons.append(f"oversold (RSI {ind.rsi14})")
    if (ind.ret_3m or 0) > 0:
        score += 1
    score += 1 if bullish > bearish else -1 if bearish > bullish else 0

    if score >= 2:
        action, conf = ("ADD" if held else "BUY"), 0.6
    elif score <= -1:
        # An owned position needs a clearer case against it than a candidate
        # needs to be passed over, so only a decisively negative read trims.
        action, conf = (("TRIM", 0.6) if score <= -2 else ("HOLD", 0.5)) if held else ("SKIP", 0.6)
    else:
        action, conf = "HOLD", 0.5
    detail = ", ".join(reasons) or "mixed signals"
    return {
        "action": action,
        "confidence": conf,
        "summary": f"Rules-based read ({detail}); AI offline.",
        "source": "rules",
    }
