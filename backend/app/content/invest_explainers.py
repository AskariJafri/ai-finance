"""Hand-written beginner explainers for every invest metric and module.

This is the no-hallucination layer: static, reviewed content — the LLM never
generates definitions. Served via GET /invest/explainers so copy fixes ship
without an app rebuild. Tone: plain language first, formula second, honest
caveat last.
"""

from __future__ import annotations

EXPLAINERS: dict[str, dict[str, str]] = {
    "ann_vol": {
        "title": "Volatility (annualized)",
        "short": "How much your portfolio's value bounces around in a typical year.",
        "body": (
            "We measure how much your portfolio's daily returns vary from day to "
            "day, then scale that to a yearly figure. 15% volatility means that in "
            "a typical year, swings of ±15% around the average are normal — not a "
            "disaster, just how bumpy the ride is. Formula: standard deviation of "
            "daily returns × √252 (there are ~252 trading days in a year)."
        ),
        "caveat": "Volatility measures BOTH up and down moves — a portfolio that rockets upward is also 'volatile'.",
    },
    "sharpe": {
        "title": "Sharpe Ratio",
        "short": "Return earned per unit of risk taken. Higher is better.",
        "body": (
            "Making 10% with wild swings is worse than making 9% smoothly. The "
            "Sharpe ratio captures that: it takes your return above the risk-free "
            "rate (what a government T-bill pays) and divides it by your "
            "volatility. Rough guide: below 0.5 is weak, around 1.0 is good, above "
            "2.0 is exceptional (and rarely lasts). Formula: (annual return − "
            "risk-free rate) ÷ annual volatility."
        ),
        "caveat": "Computed from history — a great past Sharpe does not promise a great future one.",
    },
    "sortino": {
        "title": "Sortino Ratio",
        "short": "Like Sharpe, but only punishes DOWNWARD swings.",
        "body": (
            "Investors don't mind upside surprises — only losses hurt. The Sortino "
            "ratio fixes Sharpe's blind spot by dividing excess return by "
            "*downside* deviation only (the volatility of losing days). A Sortino "
            "meaningfully higher than the Sharpe means most of the volatility is "
            "on the upside — the good kind."
        ),
        "caveat": "Needs enough losing days in the sample to be meaningful.",
    },
    "max_drawdown": {
        "title": "Maximum Drawdown",
        "short": "The worst peak-to-bottom fall your mix would have suffered.",
        "body": (
            "Imagine buying at the worst possible peak and watching your portfolio "
            "fall to its lowest trough — that percentage drop is the max drawdown. "
            "It answers the most visceral question in investing: 'how bad could it "
            "have gotten?' A 30% drawdown needs a 43% gain just to get back to "
            "even, which is why avoiding deep drawdowns matters more than chasing "
            "returns."
        ),
        "caveat": "Historical worst case — the future can always set a new record.",
    },
    "beta": {
        "title": "Beta (vs S&P 500)",
        "short": "How hard your portfolio moves when the market moves.",
        "body": (
            "Beta 1.0 means you move with the market: market drops 10%, you drop "
            "~10%. Beta 1.3 amplifies moves (drop ~13%); beta 0.5 dampens them "
            "(drop ~5%). We measure it against SPY, the S&P 500 ETF. It's the "
            "single most important number for understanding how you'll feel in a "
            "crash. Formula: covariance(your returns, market returns) ÷ "
            "variance(market returns)."
        ),
        "caveat": "Beta shifts over time and describes typical co-movement, not guarantees.",
    },
    "var_95": {
        "title": "Value at Risk — 95% (1 day)",
        "short": "On 95% of days, your one-day loss should not exceed this amount.",
        "body": (
            "We take every daily return your current mix would have had over the "
            "window, sort them worst-to-best, and read off the 5th-percentile "
            "worst day. If VaR(95%) is $1,240, then on 19 days out of 20 you "
            "shouldn't lose more than $1,240 in a single day. Banks run this exact "
            "calculation every night. Formula: −(5th percentile of daily returns) "
            "× portfolio value."
        ),
        "caveat": "Says nothing about HOW BAD the remaining 1-in-20 days can get — that's what CVaR is for.",
    },
    "var_99": {
        "title": "Value at Risk — 99% (1 day)",
        "short": "On 99% of days, your one-day loss should not exceed this amount.",
        "body": (
            "Same idea as VaR 95%, but reading the 1st-percentile worst day — the "
            "kind of day that happens roughly two or three times a year. "
            "Regulators make banks hold capital against numbers like this one."
        ),
        "caveat": "Rare events are exactly where history is the least reliable guide.",
    },
    "cvar_95": {
        "title": "CVaR / Expected Shortfall (95%)",
        "short": "WHEN one of the worst 5% of days hits, this is your average loss.",
        "body": (
            "VaR tells you where the bad tail starts; CVaR tells you how bad it is "
            "on average once you're in it. It's the average loss across the worst "
            "5% of historical days. Risk managers consider it more honest than VaR "
            "because it looks INTO the tail instead of stopping at its edge."
        ),
        "caveat": "Tail averages come from few observations — treat as an estimate, not a limit.",
    },
    "hhi": {
        "title": "Concentration (HHI)",
        "short": "How concentrated your portfolio is in few positions.",
        "body": (
            "The Herfindahl-Hirschman Index is the sum of squared position "
            "weights — the same metric antitrust regulators use for market "
            "concentration. Equal weights across 10 holdings gives 0.10; a single "
            "holding gives 1.0. We also show 'effective N' (1 ÷ HHI): a portfolio "
            "with HHI 0.25 behaves like it holds only 4 equal positions, no "
            "matter how many tickers it lists."
        ),
        "caveat": "Counts tickers, not true exposure — two tech ETFs can be one concentrated bet in disguise.",
    },
    "correlation": {
        "title": "Correlation Matrix",
        "short": "How much your holdings move together (+1) or opposite (−1).",
        "body": (
            "Diversification only works when holdings DON'T move together. +1.0 "
            "means two assets move in lockstep (no diversification benefit); 0 "
            "means unrelated; −1.0 means one zigs when the other zags (the holy "
            "grail, nearly nonexistent). If everything in your grid is above 0.8, "
            "you own one bet with several names."
        ),
        "caveat": "Correlations SPIKE toward 1 in crises — exactly when you need diversification most.",
    },
    "dca": {
        "title": "Dollar-Cost Averaging (DCA)",
        "short": "Investing a fixed amount on a schedule, regardless of price.",
        "body": (
            "Instead of guessing when to buy, you invest (say) $500 on the first "
            "of every month. When prices are high your $500 buys fewer shares; "
            "when prices crash it buys more — automatically buying more at lows. "
            "Its real power is behavioral: it removes the temptation to time the "
            "market, which most professionals fail at. Our backtest shows exactly "
            "what your schedule would have done historically, with dividends "
            "reinvested."
        ),
        "caveat": "No transaction costs or taxes modeled; past prices don't repeat.",
    },
    "lump_sum": {
        "title": "Lump Sum vs DCA",
        "short": "Investing everything at once usually beats spreading it out — with a catch.",
        "body": (
            "Because markets rise more often than they fall, investing the whole "
            "amount on day one beats DCA about two-thirds of the time in studies. "
            "So why DCA? Because the times lump-sum loses are exactly the crashes "
            "that make people panic-sell and abandon their plan. The best strategy "
            "is the one you'll actually stick with."
        ),
        "caveat": "This comparison is framing, not a recommendation.",
    },
    "monte_carlo": {
        "title": "Monte Carlo Projection",
        "short": "Thousands of simulated futures — a range of outcomes, not a prediction.",
        "body": (
            "Nobody can predict the market, so we don't try. Instead we simulate "
            "2,000+ possible futures based on how your portfolio has actually "
            "behaved (its average return and volatility), then show the range: "
            "the middle line is the median outcome; the shaded bands show where "
            "50% and 90% of simulations landed. Institutional retirement planners "
            "use exactly this method. If the 5th-percentile line still meets your "
            "goal, your plan is robust."
        ),
        "caveat": "Simulations inherit history's behavior — the future can be different from anything simulated.",
    },
    "gbm": {
        "title": "GBM Method",
        "short": "Simulates smooth randomness fitted to your portfolio's average and volatility.",
        "body": (
            "Geometric Brownian Motion is the textbook model of market randomness "
            "(it underlies the Black-Scholes option formula). Each simulated day "
            "draws a return from a bell curve fitted to your portfolio's history. "
            "Clean and standard, but bell curves underestimate extreme days."
        ),
        "caveat": "Real markets have fatter tails than a bell curve — compare with Bootstrap.",
    },
    "bootstrap": {
        "title": "Bootstrap Method",
        "short": "Simulates the future by reshuffling your portfolio's ACTUAL past days.",
        "body": (
            "Instead of assuming a bell curve, bootstrap resampling draws randomly "
            "from the daily returns your portfolio actually experienced — "
            "including its real crash days. Fat tails and asset correlations come "
            "along for free because they're baked into the real data. If GBM and "
            "Bootstrap disagree a lot, your history has extreme days a bell curve "
            "can't capture."
        ),
        "caveat": "Can only reshuffle what happened — a never-before-seen shock isn't in the deck.",
    },
    "sma_crossover": {
        "title": "SMA Crossover Strategy",
        "short": "In the market when the short average is above the long one; in cash otherwise.",
        "body": (
            "A Simple Moving Average smooths prices over N days. The classic "
            "50/200 crossover holds stocks while the 50-day average is above the "
            "200-day ('golden cross') and retreats to cash when it falls below "
            "('death cross'). It's the most-studied trend-following rule: it "
            "often reduces deep drawdowns at the cost of whipsaw losses in "
            "sideways markets. Our version trades on yesterday's signal — no "
            "cheating with hindsight."
        ),
        "caveat": "Backtest excludes trading costs and taxes, which hit active strategies hardest.",
    },
    "cagr": {
        "title": "CAGR",
        "short": "The single smoothed yearly growth rate your money actually achieved.",
        "body": (
            "Compound Annual Growth Rate answers: 'what constant yearly return "
            "would have turned my starting amount into my ending amount?' It "
            "smooths the bumpy ride into one comparable number. Formula: "
            "(ending ÷ starting)^(1/years) − 1."
        ),
        "caveat": "Two investments with identical CAGR can have wildly different risk — read it beside volatility and drawdown.",
    },
    "rebalancing": {
        "title": "Rebalancing",
        "short": "Selling what grew and buying what lagged, back to your target mix.",
        "body": (
            "You chose target weights for a reason (say 60% stocks / 40% bonds). "
            "When stocks rally, they become an oversized share of your portfolio — "
            "you're now taking more risk than you chose. Rebalancing trims winners "
            "and tops up laggards back to target. It enforces 'buy low, sell high' "
            "mechanically, with zero forecasting. We propose the trades; nothing "
            "executes until you approve each one."
        ),
        "caveat": "In taxable accounts, selling winners can trigger capital-gains tax (not modeled here).",
    },
    "how_it_works": {
        "title": "How this app trades (it doesn't)",
        "short": "The app analyzes and recommends; YOU place trades at your own broker.",
        "body": (
            "You tell the app what you own (Holdings), and it prices everything "
            "from public market data — Canadian listings like VFV.TO in CAD, US "
            "ones like SPY converted at the live exchange rate. From there it "
            "runs the same analysis a bank's risk desk does. When your portfolio "
            "drifts from your targets, it produces a trade checklist: what to "
            "buy or sell and why. You place those trades at your own broker "
            "(Wealthsimple, Questrade, TD…), tap 'I placed this', and your "
            "holdings update. No broker keys, no automated execution, nothing "
            "that can trade behind your back."
        ),
        "caveat": "Holdings are only as accurate as what you enter — update them after trades you make outside the app.",
    },
    "risk_free_rate": {
        "title": "Risk-Free Rate",
        "short": "What 'no-risk' money earns — the bar every investment must clear.",
        "body": (
            "Government T-bills pay a return with essentially zero default risk. "
            "Any risky investment must be judged by how much it beats that. We "
            "use a fixed 4% assumption in Sharpe/Sortino calculations — a stated "
            "assumption, not a live feed, so the math is transparent."
        ),
        "caveat": "When actual rates differ from 4%, ratio values shift slightly; comparisons between your own portfolios remain valid.",
    },
    "fan_chart": {
        "title": "Reading the Fan Chart",
        "short": "The bands show where most simulated futures landed — wider = less certain.",
        "body": (
            "The solid middle line is the median simulation: half did better, "
            "half worse. The inner band holds the middle 50% of outcomes "
            "(25th-75th percentile); the outer band holds 90% of them (5th-95th). "
            "The fan widens over time because uncertainty compounds — that "
            "widening IS the honest admission that long-term outcomes vary "
            "enormously. Central banks present inflation forecasts exactly this "
            "way."
        ),
        "caveat": "5% of simulations ended OUTSIDE the outer band — the real future might too.",
    },
    "stress_test": {
        "title": "Stress Scenarios",
        "short": "Replays history's worst storms against your current portfolio.",
        "body": (
            "Banks are legally required to test their books against scenarios "
            "like 2008. We do the same for you: each scenario applies the "
            "historical shock to your holdings — equities scaled by their beta, "
            "bonds by bond-market moves. The result is a first-order estimate of "
            "the damage: 'if 2008 happened tomorrow, roughly what would my "
            "portfolio lose?' Recovery times shown are for the INDEX in that "
            "historical episode — your portfolio and the next crisis will differ."
        ),
        "caveat": "First-order estimates: real crises break correlations in ways no simple model captures.",
    },
    "drift": {
        "title": "Allocation Drift",
        "short": "How far your actual weights have wandered from your targets.",
        "body": (
            "Markets move every day, so your portfolio's weights drift from the "
            "targets you set. Drift of a few percent is noise; sustained drift "
            "past ~5% means your risk profile has genuinely changed without you "
            "deciding it. That 5% threshold is the standard trigger institutional "
            "managers use — and the default in our rebalance proposer."
        ),
        "caveat": "Rebalancing too often burns money on spreads; once a quarter or at 5% drift is a common policy.",
    },
}
