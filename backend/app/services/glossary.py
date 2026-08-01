"""Retrieves hand-written explainers relevant to a question.

The explainer tables (``invest_explainers`` for portfolio metrics,
``money_explainers`` for cash, budgeting and order mechanics) are the app's
no-hallucination layer: static, reviewed definitions. The chat assistant leans
on them twice —

1. matched entries are injected into the prompt as the *authoritative* wording,
   so the model paraphrases reviewed copy instead of inventing a definition;
2. if the model is unavailable, a match is served verbatim, which means "what
   is a Sharpe ratio?" still gets a correct answer with the LLM switched off.

Matching is deliberately dumb (alias phrases, longest first). A vector index
would be overkill for two dozen entries and would add a dependency for no
measurable gain.
"""

from __future__ import annotations

import re

from app.content.invest_explainers import EXPLAINERS
from app.content.money_explainers import MONEY_EXPLAINERS

# One lookup table for matching. Kept as two source files because only the
# invest half is served to the app's metric tooltips.
_ENTRIES: dict[str, dict[str, str]] = {**EXPLAINERS, **MONEY_EXPLAINERS}

# Phrases that should resolve to an explainer key. The key itself and its title
# are added automatically below; this table only carries the wording a person
# actually types. Keep them specific — a phrase as short as "needs" would match
# half of all questions and crowd out the entry the user actually asked about.
_ALIASES: dict[str, tuple[str, ...]] = {
    "ann_vol": ("volatility", "volatile", "standard deviation", "how bumpy"),
    "sharpe": ("sharpe", "risk adjusted return", "risk-adjusted return"),
    "sortino": ("sortino", "downside deviation"),
    "max_drawdown": ("drawdown", "worst loss", "peak to trough"),
    "beta": ("beta", "market sensitivity"),
    "var_95": ("value at risk", "var 95", "var95"),
    "var_99": ("var 99", "var99", "tail risk"),
    "cvar_95": ("cvar", "conditional value at risk", "expected shortfall"),
    "hhi": ("hhi", "herfindahl", "concentration", "diversification", "diversified"),
    "correlation": ("correlation", "correlated", "move together"),
    "dca": ("dca", "dollar cost averaging", "dollar-cost averaging", "averaging in"),
    "lump_sum": ("lump sum", "all at once", "invest it all"),
    "monte_carlo": ("monte carlo", "simulation", "projection"),
    "gbm": ("geometric brownian", "gbm", "random walk"),
    "bootstrap": ("bootstrap", "resampling", "resample"),
    "sma_crossover": (
        "sma",
        "moving average",
        "golden cross",
        "death cross",
        "crossover",
    ),
    "cagr": ("cagr", "compound annual", "annualized return", "annualised return"),
    "rebalancing": ("rebalanc", "back to target", "trim winners"),
    "how_it_works": ("how does this app", "how do you calculate", "where do the numbers"),
    "risk_free_rate": ("risk free", "risk-free", "t-bill", "treasury bill"),
    "fan_chart": ("fan chart", "percentile band", "confidence band"),
    "stress_test": ("stress test", "crash scenario", "what if the market"),
    "drift": ("drift", "off target", "weights moved"),
    "rule_503020": (
        "50/30/20",
        "50 30 20",
        "budget rule",
        "budgeting rule",
    ),
    "savings_rate": ("savings rate", "saving rate", "how much am i saving"),
    "net_cash_flow": ("cash flow", "cashflow", "money left over", "left over"),
    "needs_vs_wants": (
        "needs vs wants",
        "needs and wants",
        "wants vs needs",
        "discretionary spending",
    ),
    "emergency_fund": ("emergency fund", "rainy day", "safety net"),
    "tfsa": ("tfsa", "tax free savings", "tax-free savings"),
    "rrsp": ("rrsp", "registered retirement", "retirement savings plan"),
    "market_order": ("market order",),
    "limit_order": ("limit order",),
    "getting_started": (
        "start investing",
        "started investing",
        "getting started",
        "new to investing",
        "begin investing",
        "first investment",
    ),
}


def _index() -> list[tuple[str, str]]:
    """(phrase, key) pairs, longest phrase first so 'value at risk' beats 'var'."""
    pairs: list[tuple[str, str]] = []
    for key, entry in _ENTRIES.items():
        phrases = {key.replace("_", " "), entry["title"].lower()}
        phrases.update(_ALIASES.get(key, ()))
        # "Volatility (annualized)" -> also match the bare "volatility".
        bare = re.sub(r"\s*\(.*?\)", "", entry["title"]).strip().lower()
        if bare:
            phrases.add(bare)
        pairs += [(p, key) for p in phrases if p]
    return sorted(pairs, key=lambda pair: -len(pair[0]))


_INDEX = _index()


def match_terms(question: str, limit: int = 3) -> list[str]:
    """Explainer keys the question appears to be asking about, best first."""
    text = question.lower()
    hits: list[str] = []
    for phrase, key in _INDEX:
        if key in hits:
            continue
        if phrase in text:
            hits.append(key)
            if len(hits) >= limit:
                break
    return hits


def render_reference(keys: list[str]) -> str:
    """The matched entries, formatted for the prompt's reference block."""
    blocks = []
    for key in keys:
        entry = _ENTRIES.get(key)
        if entry is None:
            continue
        blocks.append(
            f"{entry['title']} — {entry['short']}\n{entry['body']}\nCaveat: {entry['caveat']}"
        )
    return "\n\n".join(blocks)


def render_answer(keys: list[str]) -> str:
    """A complete, user-facing answer built only from reviewed copy.

    Used when the model is unavailable, so a definition question still gets a
    correct answer rather than an error.
    """
    parts = []
    for key in keys:
        entry = _ENTRIES.get(key)
        if entry is None:
            continue
        parts.append(f"**{entry['title']}**\n\n{entry['body']}\n\n_{entry['caveat']}_")
    return "\n\n".join(parts)
