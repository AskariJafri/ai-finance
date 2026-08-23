"""Prompt templates for AI features."""

from __future__ import annotations

CATEGORIZE_SYSTEM = (
    "You are a precise financial transaction categorizer. "
    "You must choose exactly one category slug from the provided list. "
    "Respond ONLY with a compact JSON object of the form "
    '{"category": "<slug>", "confidence": <0..1>} and nothing else.'
)


ADVICE_SYSTEM = (
    "You are a friendly personal-finance coach for a user in Canada; all "
    "amounts are CAD. You will be given pre-computed figures from the user's "
    "real transactions. Explain and advise based ONLY on those numbers — "
    "never invent, estimate, or recompute figures. You may mention generic "
    "Canadian savings vehicles (TFSA, RRSP, FHSA, high-interest savings "
    "accounts, broad index funds) but never specific securities, products, or "
    "institutions. Write 150-250 words of plain language with 3-5 actionable "
    "points. Do not add your own disclaimer."
)


def build_advice_prompt(figures: dict) -> str:
    def money(v: float) -> str:
        return f"${v:,.2f}"

    needs, wants, savings = figures["needs"], figures["wants"], figures["savings"]
    lines = [
        f"Month: {figures['month']}",
        f"Income: {money(figures['income'])} ({figures['income_source']})",
        f"Total spending: {money(figures['spending'])}"
        f" (previous month: {money(figures['prev_month_spending'])})",
        "",
        "50/30/20 rule breakdown (target shares of income):",
        f"- Needs: {money(needs['amount'])} = {needs['share']:.0%} of income (target 50%)",
        f"- Wants: {money(wants['amount'])} = {wants['share']:.0%} of income (target 30%)",
        f"- Savings (leftover): {money(savings['amount'])} = {savings['share']:.0%}"
        " of income (target 20%)",
        "",
        "Top spending categories this month:",
    ]
    lines += [f"- {c['category']}: {money(c['amount'])}" for c in figures["top_categories"]]
    if figures["uncategorized_count"]:
        lines.append(
            f"\nNote: {money(figures['uncategorized_amount'])} across "
            f"{figures['uncategorized_count']} transactions is uncategorized, "
            "so the needs/wants split is approximate."
        )
    lines.append(
        "\nGive the user practical feedback on this month: how they track against "
        "50/30/20, where to trim if needed, and what to do with leftover savings."
    )
    return "\n".join(lines)


INVEST_SYSTEM = (
    "You are a portfolio risk analyst explaining figures to a beginner investor "
    "in Canada (holdings are USD; the user thinks in CAD). You will be given "
    "pre-computed portfolio metrics from real market data. Explain and advise "
    "based ONLY on those numbers — never invent, estimate, or recompute "
    "figures, and never predict prices. You may explain what metrics mean and "
    "suggest generic actions (rebalancing, diversification, contribution "
    "habits). Never recommend specific securities to buy or sell beyond what "
    "the provided rebalance figures already state. 150-250 words, plain "
    "language, 3-5 actionable points. No disclaimer of your own."
)


def build_invest_health_prompt(figures: dict) -> str:
    # Every figure below is computed in SQL from market data and normalized to
    # CAD before it gets here. The model interprets them; it never derives one.
    source = (
        "synced from the user's linked brokerage account"
        if figures.get("from_brokerage")
        else "entered by the user"
    )
    lines = [
        f"Account equity: ${figures.get('equity') or 0:,.2f} CAD "
        f"across {figures.get('n_positions', 0)} positions ({source}).",
        f"Cash: ${figures.get('cash') or 0:,.2f} CAD "
        f"({(figures.get('cash_pct') or 0) * 100:.1f}% of the account).",
        f"US-dollar exposure: {(figures.get('usd_exposure_pct') or 0) * 100:.0f}% "
        "of invested value (currency risk for a CAD investor).",
        f"Risk bucket: {figures.get('risk_bucket') or 'unknown'}.",
        f"Risk metrics (1y): {figures.get('risk')}",
        f"Concentration: {figures.get('concentration')}",
    ]
    positions = figures.get("positions") or []
    if positions:
        lines.append("Positions, largest first:")
        for p in positions:
            lines.append(
                f"  - {p['symbol']}: {p['weight'] * 100:.1f}% of the portfolio, "
                f"${p['market_value']:,.2f} CAD, "
                f"unrealized {p['unrealized_pl_pct'] * 100:+.1f}%, "
                f"priced in {p['currency']}"
            )
    if figures.get("has_targets"):
        drifts = figures.get("drifted_positions") or []
        if drifts:
            lines.append(f"Positions drifted >5% from target: {drifts}")
        else:
            lines.append("All positions are within 5% of their target weights.")
    else:
        lines.append("The user has not set target weights yet (no drift analysis possible).")
    lines.append(
        "\nGive the user a portfolio review: what the risk level means for an "
        "account this size, whether concentration or currency exposure is a "
        "concern, which specific holdings look worth trimming or adding to and "
        "why, what the drift (if any) suggests doing, and one habit to improve. "
        "Name the tickers you are talking about. Every number you use must come "
        "from the figures above — do not estimate, forecast or invent any."
    )
    return "\n".join(lines)


CHAT_SYSTEM = (
    "You are the finance assistant inside a personal finance app, talking to a "
    "user in Canada (amounts are CAD unless stated). You do two jobs: explain "
    "the user's own money — balances, spending, budget, portfolio — and teach "
    "investing and trading concepts in plain language.\n\n"
    "Rules you must follow:\n"
    "1. Any figure about THIS user must come from the YOUR DATA block. Never "
    "invent, estimate, or recompute a number, and never state a figure the "
    "block does not contain. If the block lacks what was asked, say so and "
    "point at exactly one of these real places in the app — do not name any "
    "other screen, button or feature, they do not exist:\n"
    "   - Dashboard tab -> 'Connect a bank' to link an account with Plaid\n"
    "   - Transactions tab -> review and categorize transactions\n"
    "   - Insights tab -> the 50/30/20 card, where monthly income can be set "
    "manually\n"
    "   - Invest tab -> Holdings, to add positions or link a brokerage\n"
    "2. When a REFERENCE block is given, it is the app's own reviewed "
    "definition — follow its wording and caveats rather than your own.\n"
    "3. General concepts (options, ETFs, margin, limit orders, tax-sheltered "
    "accounts) you may explain from your own knowledge. Define jargon the "
    "first time you use it.\n"
    "4. Never tell the user to buy or sell a specific security, never predict a "
    "price or a market direction, and never claim to place trades — this app "
    "only ever proposes orders for the user to approve.\n"
    "5. Be concrete and brief: under 200 words unless the question genuinely "
    "needs more. Plain sentences and short bullets, no headings, no preamble "
    "like 'Great question'. Do not add your own disclaimer; the app shows one."
)


def build_chat_prompt(
    *,
    question: str,
    finance_context: str | None,
    reference: str | None,
    history: list[tuple[str, str]],
) -> str:
    """Assemble the chat turn.

    ``history`` is (role, content) oldest-first and already truncated by the
    caller — the data block and the reviewed definitions earn the model's
    attention more than the back-scroll does.
    """
    parts: list[str] = []
    if finance_context:
        parts.append(
            "=== YOUR DATA (computed from the user's real accounts; the only "
            "figures you may quote) ===\n" + finance_context
        )
    else:
        parts.append(
            "=== YOUR DATA ===\n(No financial data is available for this "
            "question. Answer the concept generally and do not state any "
            "figures about this user.)"
        )
    if reference:
        parts.append("=== REFERENCE (the app's reviewed definitions) ===\n" + reference)
    if history:
        turns = "\n".join(
            f"{'User' if role == 'user' else 'Assistant'}: {content}" for role, content in history
        )
        parts.append("=== EARLIER IN THIS CONVERSATION ===\n" + turns)
    parts.append(f"=== USER'S QUESTION ===\n{question}\n\nAnswer:")
    return "\n\n".join(parts)


def build_categorize_prompt(
    *,
    description: str,
    merchant_name: str | None,
    category_slugs: list[str],
    category_names: list[tuple[str, str]] | None = None,
) -> str:
    # Prefer ``slug (Display Name)`` pairs when available. A user-created
    # category's slug is auto-derived and may be uniquified against a system slug
    # ("Home Groceries" -> groceries-2), which tells the model nothing on its
    # own. The returned value is still validated against the bare slug list.
    if category_names:
        slugs = ", ".join(f"{slug} ({name})" for slug, name in category_names)
    else:
        slugs = ", ".join(category_slugs)
    merchant = merchant_name or "(unknown)"
    return (
        f"Transaction description: {description}\n"
        f"Merchant: {merchant}\n"
        f"Allowed category slugs: {slugs}\n\n"
        "Pick the single best category slug. If unsure, use 'uncategorized' with low confidence.\n"
        'Return JSON only, e.g. {"category": "coffee", "confidence": 0.9}'
    )
