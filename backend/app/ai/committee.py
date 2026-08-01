"""AI Investment Committee — several role-playing agents reason over the SAME
real numbers (a live quote + technical indicators) and a CIO synthesizes a
verdict: BUY / SKIP / HOLD.

Design rules mirrored from the rest of the app:
- The LLM never invents figures — every number in the prompts comes from
  `Indicators` / the quote. Agents only *interpret*.
- Provider-agnostic: takes an `LLMProvider`, so it runs on local Ollama (free)
  or any future backend, no code change.
- Never fails the request: bad JSON from a small local model is salvaged, and
  if the LLM is unavailable the CIO verdict falls back to a transparent
  rules-based read of the indicators.
- Educational only — not investment advice. Paper/sandbox context.
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


_JSON_RULE = (
    "Respond with ONLY a JSON object, no prose, no markdown: "
    '{"stance": "<bullish|bearish|neutral|caution>", '
    '"confidence": <0..1>, "thesis": "<=40 words"}'
)


async def _run_agent(llm: LLMProvider, key: str, name: str, system: str, prompt: str) -> AgentTake:
    try:
        raw = await llm.complete(prompt + "\n\n" + _JSON_RULE, system=system, temperature=0.3)
        data = _parse_json(raw)
    except Exception as exc:  # noqa: BLE001 — any LLM error degrades this agent only
        logger.warning("Committee agent %s failed: %s", key, exc)
        data = {}
    stance = str(data.get("stance", "neutral")).lower()
    if stance not in {"bullish", "bearish", "neutral", "caution"}:
        stance = "neutral"
    thesis = str(data.get("thesis") or "No read available (model unavailable).").strip()
    return AgentTake(key, name, stance, round(_clip(data.get("confidence")), 2), thesis[:400])


async def run_committee(symbol: str, quote: dict, ind: Indicators, llm: LLMProvider) -> dict:
    ctx = _context(symbol, quote, ind)

    specs = [
        (
            "research",
            "Research Analyst",
            "You are a Senior Research Analyst. Judge whether the price trend and momentum justify owning this now. Be precise and skeptical.",
            f"Assess {symbol} as an investment from this data:\n{ctx}",
        ),
        (
            "technical",
            "Technical Analyst",
            "You are a Technical Analyst. Read momentum, trend and overbought/oversold from RSI and moving averages.",
            f"Technical read for {symbol}:\n{ctx}\nIs momentum favorable or stretched?",
        ),
        (
            "risk",
            "Risk Officer",
            "You are the Chief Risk Officer. Your only job is protecting capital. Weigh volatility and drawdown.",
            f"Risk of buying {symbol} now:\n{ctx}\nHow much could this hurt?",
        ),
        (
            "bear",
            "Devil's Advocate",
            "You are the Devil's Advocate. Attack the bull case and name the failure scenarios.",
            f"Make the bear case against buying {symbol}:\n{ctx}",
        ),
        (
            "pm",
            "Portfolio Manager",
            "You are the Portfolio Manager sizing positions. Balance conviction against risk; small size when uncertain.",
            f"Would you allocate to {symbol}, and how boldly?\n{ctx}",
        ),
    ]

    takes = await asyncio.gather(
        *(_run_agent(llm, key, name, system, prompt) for key, name, system, prompt in specs)
    )

    cio = await _cio(symbol, quote, ind, takes, llm)
    return {
        "symbol": symbol,
        "price": quote.get("price"),
        "currency": quote.get("currency", "USD"),
        "action": cio["action"],
        "confidence": cio["confidence"],
        "summary": cio["summary"],
        "source": cio["source"],
        "agents": [t.__dict__ for t in takes],
        "indicators": ind.as_dict(),
    }


async def run_committee_many(
    items: list[tuple[str, dict, Indicators]],
    llm: LLMProvider,
    *,
    use_ai: bool = True,
) -> list[dict]:
    """Deliberate over several tickers concurrently.

    Each ticker degrades on its own: a committee that errors falls back to
    ``rules_only`` for that symbol rather than losing the whole batch. Keep the
    list short — every ticker costs six LLM calls.
    """

    async def one(symbol: str, quote: dict, ind: Indicators) -> dict:
        if use_ai:
            try:
                return await run_committee(symbol, quote, ind, llm)
            except Exception as exc:  # noqa: BLE001 — degrade this symbol only
                logger.warning("Committee failed for %s, using rules: %s", symbol, exc)
        return rules_only(symbol, quote, ind)

    return list(await asyncio.gather(*(one(s, q, i) for s, q, i in items)))


async def _cio(symbol, quote, ind: Indicators, takes: list[AgentTake], llm: LLMProvider) -> dict:
    bullish = sum(1 for t in takes if t.stance == "bullish")
    bearish = sum(1 for t in takes if t.stance in ("bearish", "caution"))
    board = "; ".join(f"{t.name}: {t.stance} — {t.thesis}" for t in takes)

    system = (
        "You are the Chief Investment Officer chairing the committee. Capital "
        "preservation beats returns; when uncertain, SKIP. Weigh every member."
    )
    prompt = (
        f"Final decision on {symbol} @ {quote.get('price')} {quote.get('currency', '')}.\n"
        f"Committee: {board}\n"
        f"Data: {_context(symbol, quote, ind)}\n\n"
        'Respond with ONLY JSON: {"action":"<BUY|SKIP|HOLD>","confidence":<0..1>,'
        '"rationale":"<=60 words"}'
    )
    try:
        raw = await llm.complete(prompt, system=system, temperature=0.2)
        data = _parse_json(raw)
        action = str(data.get("action", "")).upper()
        if action in VALID_ACTIONS and data.get("rationale"):
            return {
                "action": action,
                "confidence": round(_clip(data.get("confidence")), 2),
                "summary": str(data["rationale"]).strip()[:500],
                "source": "ai",
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("CIO synthesis failed, using rules: %s", exc)

    return _rules_verdict(ind, bullish, bearish)


def rules_only(symbol: str, quote: dict, ind: Indicators) -> dict:
    """Full committee result without any LLM call — used when AI is disabled or
    unreachable, so the feature still works (transparently rules-based)."""
    stance = "bullish" if ind.trend == "up" else "bearish" if ind.trend == "down" else "neutral"
    names = [
        ("research", "Research Analyst"),
        ("technical", "Technical Analyst"),
        ("risk", "Risk Officer"),
        ("bear", "Devil's Advocate"),
        ("pm", "Portfolio Manager"),
    ]
    takes = [
        AgentTake(k, n, stance, 0.5, "AI offline — see the rules-based verdict below.")
        for k, n in names
    ]
    verdict = _rules_verdict(ind, 1 if stance == "bullish" else 0, 1 if stance == "bearish" else 0)
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


def _rules_verdict(ind: Indicators, bullish: int, bearish: int) -> dict:
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
        action, conf = "BUY", 0.6
    elif score <= -1:
        action, conf = "SKIP", 0.6
    else:
        action, conf = "HOLD", 0.5
    detail = ", ".join(reasons) or "mixed signals"
    return {
        "action": action,
        "confidence": conf,
        "summary": f"Rules-based read ({detail}); AI offline.",
        "source": "rules",
    }
