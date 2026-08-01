"""Hand-written explainers for the cash, budgeting and order-mechanics terms.

Same no-hallucination role as ``invest_explainers`` — static, reviewed copy the
LLM paraphrases rather than invents, and serves verbatim when it is offline —
but scoped to the questions the chat assistant raises on its own: its starter
prompts offer "Market vs limit orders", "TFSA vs RRSP" and "How do I start
investing?", and the answers must not depend on a local model being up.

Deliberately kept out of ``invest_explainers`` so ``GET /invest/explainers``
(which the Invest screens key into for metric tooltips) stays a list of
portfolio *metrics*. ``glossary`` merges both tables.

Canadian by default — the user's accounts, currency and tax shelters are CA.
Tone follows the invest table: plain language first, mechanics second, honest
caveat last. Nothing here is personal advice; the caveats say so where the
topic (RRSP vs TFSA especially) genuinely depends on someone's own situation.
"""

from __future__ import annotations

MONEY_EXPLAINERS: dict[str, dict[str, str]] = {
    "rule_503020": {
        "title": "The 50/30/20 Rule",
        "short": "A starting shape for a month: 50% needs, 30% wants, 20% saved.",
        "body": (
            "A budgeting rule of thumb popularized by Elizabeth Warren: of your "
            "after-tax income, roughly 50% goes to needs (rent, groceries, "
            "utilities, transport, insurance, minimum debt payments), 30% to "
            "wants (dining out, subscriptions, travel, shopping) and 20% to "
            "saving and paying debt down faster. The app sorts your categorized "
            "spending into needs and wants, then reports what's left as the "
            "savings share. Its value isn't the exact numbers — it's that it "
            "turns a pile of transactions into three figures you can actually "
            "act on."
        ),
        "caveat": "It's a rule of thumb, not a law: in an expensive city, needs above 50% can be unavoidable rather than a failure. Only categorized spending can be split, so a month with many uncategorized transactions skews the shares.",
    },
    "savings_rate": {
        "title": "Savings Rate",
        "short": "The share of the money that came in that you didn't spend.",
        "body": (
            "Money in minus money out, divided by money in, over the period "
            "shown. A 20% savings rate means one dollar in five stayed. It is "
            "the single most informative number in personal finance because it "
            "moves both levers at once: every dollar not spent is a dollar "
            "saved AND a dollar less you need each year in retirement. The app "
            "computes it from your synced transactions, not from anything you "
            "self-report."
        ),
        "caveat": "A transfer between your own accounts is not income or spending — the app filters those out, but a mislabelled transfer can distort the rate for that month.",
    },
    "net_cash_flow": {
        "title": "Net Cash Flow",
        "short": "Money in minus money out — what the month actually left over.",
        "body": (
            "The plain difference between what arrived and what left across your "
            "linked accounts in the period. Positive means you finished ahead; "
            "negative means the gap was covered by savings or credit. Unlike a "
            "balance, which is a snapshot, cash flow is the direction you're "
            "travelling — a large balance with persistently negative cash flow "
            "is a slow leak."
        ),
        "caveat": "Credit-card and loan inflows are excluded from income on purpose — borrowed money isn't earnings, and counting it would flatter the figure.",
    },
    "needs_vs_wants": {
        "title": "Needs vs Wants",
        "short": "Needs keep life running; wants are the ones you could pause.",
        "body": (
            "The split behind the 50/30/20 view. Needs are the obligations that "
            "would have real consequences if unpaid: housing, groceries, "
            "utilities, transport, insurance, minimum debt payments. Wants are "
            "discretionary: restaurants, subscriptions, travel, entertainment, "
            "shopping. The app assigns each spending category to one bucket, so "
            "the split is consistent month to month rather than re-argued each "
            "time."
        ),
        "caveat": "The line is genuinely blurry — a car is a need in one life and a want in another. Recategorizing a transaction moves it between buckets.",
    },
    "emergency_fund": {
        "title": "Emergency Fund",
        "short": "Cash set aside so a bad month doesn't become debt.",
        "body": (
            "Three to six months of essential expenses (your *needs*, not your "
            "whole spend), kept somewhere boring and instantly accessible — a "
            "high-interest savings account, not investments. Its job is not to "
            "earn; it's to make a job loss, a car repair or a medical bill a "
            "nuisance instead of a credit-card balance. Most guidance treats it "
            "as the thing to build before investing seriously, because selling "
            "investments at a bad moment is how temporary losses become "
            "permanent ones."
        ),
        "caveat": "Sizing depends on how stable your income is: one salaried earner with steady work may hold less than a self-employed household with variable income.",
    },
    "tfsa": {
        "title": "TFSA (Tax-Free Savings Account)",
        "short": "A Canadian account where growth and withdrawals are never taxed.",
        "body": (
            "Despite the name it's a shelter, not a savings account — it can "
            "hold cash, GICs, ETFs or stocks. You contribute after-tax dollars, "
            "and everything after that is tax-free: no tax on growth, no tax on "
            "withdrawal, and withdrawals don't count as income (so they don't "
            "affect income-tested benefits). Contribution room accumulates each "
            "year from age 18, unused room carries forward, and anything you "
            "withdraw is added back to your room — but only on January 1 of the "
            "following year."
        ),
        "caveat": "Re-contributing a withdrawal in the same calendar year is the classic over-contribution mistake, penalized at 1% per month. Check your own room in CRA My Account — the app doesn't track it.",
    },
    "rrsp": {
        "title": "RRSP (Registered Retirement Savings Plan)",
        "short": "A Canadian account that defers tax: deduct now, pay tax on withdrawal.",
        "body": (
            "Contributions are deducted from your taxable income in the year you "
            "make them, growth is untaxed inside the account, and withdrawals "
            "are taxed as regular income later. That makes it a bet on tax "
            "rates: it works best when you contribute in high-earning years and "
            "withdraw in lower-income retirement years. Room is 18% of the prior "
            "year's earned income up to an annual cap, less any pension "
            "adjustment. Withdrawing early is costly — you're taxed at your "
            "current rate and the room is gone for good (the Home Buyers' Plan "
            "and Lifelong Learning Plan are the deliberate exceptions)."
        ),
        "caveat": "TFSA vs RRSP depends on your marginal tax rate now versus in retirement, employer matching, and benefit clawbacks — it's a genuinely personal call worth taking to an accountant.",
    },
    "market_order": {
        "title": "Market Order",
        "short": "Buy or sell right now, at whatever the going price is.",
        "body": (
            "You specify the quantity, not the price: the order fills "
            "immediately against the best available prices. It's the right "
            "choice when you care that the trade happens more than what it "
            "costs to the penny — typically a liquid, heavily-traded ETF during "
            "normal market hours, where the gap between bid and ask is a cent "
            "or two."
        ),
        "caveat": "You have no price guarantee. On a thinly-traded stock, at the open/close, or during a fast-moving day, a market order can fill well away from the quote you saw.",
    },
    "limit_order": {
        "title": "Limit Order",
        "short": "Name your worst acceptable price; it fills only at that price or better.",
        "body": (
            "A buy limit fills at your price or lower, a sell limit at your "
            "price or higher. You trade certainty of execution for certainty of "
            "price — the opposite of a market order. This is the safer default "
            "for anything with a wide bid-ask spread, low volume, or when "
            "you're placing an order while the market is closed and the next "
            "open could gap. Set it near the current quote if you actually want "
            "it to fill; set it far away and it simply won't."
        ),
        "caveat": "It may never fill, or fill only partly — and an unfilled limit order on a rising position means you missed the trade entirely, which is its own kind of cost.",
    },
    "getting_started": {
        "title": "Getting Started Investing (Canada)",
        "short": "Emergency fund first, then a tax shelter, then a broad low-fee fund.",
        "body": (
            "The conventional sequence: clear high-interest debt (a 20% credit "
            "card beats any expected market return), hold an emergency fund in "
            "cash, then invest inside a shelter — usually a TFSA first, because "
            "it's flexible and withdrawals are tax-free. Inside it, most "
            "beginners are served by one broad, low-fee, globally-diversified "
            "index ETF (or an all-in-one asset-allocation ETF, which handles "
            "the stock/bond mix and rebalancing for you) bought on a regular "
            "schedule. Fees compound exactly like returns do, in the wrong "
            "direction — the difference between a 0.20% and a 2.00% MER over "
            "decades is enormous. Decide your stock/bond mix by how much of a "
            "drop you could sit through without selling."
        ),
        "caveat": "General information, not a recommendation of any specific fund or allocation. This app never places trades for you.",
    },
}
