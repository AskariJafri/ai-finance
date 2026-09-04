"""Global stress-scenario library, applied to the current portfolio.

Each scenario's shocks are HARDCODED from the historical record (sources in
comments) — no model, no AI. Equity positions are shocked by
equity_shock * beta_i, bond positions by bond_shock, and any position held in a
currency other than the reporting one additionally takes fx_shock.

The market and FX legs COMPOUND — (1+market)(1+fx)-1 — rather than adding.
A crash and a currency move are separate events applying to the same capital,
and adding them overstates a joint move by the cross term (a -57% equity shock
with a -15% FX shock is -63.5%, not -72%).

Recovery figures describe the INDEX's historical recovery, never the user's
portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass

# ETFs treated as bond exposure for shock purposes.
BOND_ETFS = frozenset(
    {
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
        "XBB",
        "ZAG",
        "VAB",
        "VSB",
    }  # incl. Canadian aggregate bond ETFs
)


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    description: str
    source: str
    equity_shock: float  # applied x beta to equity positions
    bond_shock: float
    fx_shock: float  # applied to the CAD-converted figure only
    recovery_index: str
    recovery_months: int | None
    recovery_note: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="gfc_2008",
        name="2008 Global Financial Crisis",
        description=(
            "The housing-credit collapse. US stocks fell about 57% from the "
            "October 2007 peak to the March 2009 trough; high-quality bonds "
            "rose as investors fled to safety."
        ),
        # S&P 500: 1,565 (Oct 2007) -> 677 (Mar 2009) = -56.8%; Bloomberg US
        # Agg returned ~+5% in 2008.
        source="S&P 500 peak-to-trough 2007-2009; Bloomberg US Aggregate 2008",
        equity_shock=-0.57,
        bond_shock=0.05,
        fx_shock=0.0,
        recovery_index="S&P 500",
        recovery_months=49,
        recovery_note="The S&P 500 regained its 2007 peak in about 49 months (March 2013).",
    ),
    Scenario(
        key="covid_2020",
        name="2020 COVID Crash",
        description=(
            "The fastest bear market in history: US stocks dropped about 34% "
            "in 23 trading days as the pandemic hit, then recovered in months "
            "on unprecedented stimulus."
        ),
        # S&P 500: 3,386 (Feb 19) -> 2,237 (Mar 23) = -33.9%.
        source="S&P 500, Feb 19 - Mar 23 2020",
        equity_shock=-0.34,
        bond_shock=0.01,
        fx_shock=-0.05,
        recovery_index="S&P 500",
        recovery_months=5,
        recovery_note="The S&P 500 regained its February 2020 peak by August 2020 (~5 months).",
    ),
    Scenario(
        key="rates_2022",
        name="2022 Rate Shock",
        description=(
            "Central banks raised rates at the fastest pace in decades to "
            "fight inflation. Unusually, stocks AND bonds fell together — "
            "the classic 60/40 hedge failed."
        ),
        # S&P 500 -25.4% peak-to-trough 2022; Bloomberg US Agg -17% at worst.
        source="S&P 500 and Bloomberg US Aggregate, 2022",
        equity_shock=-0.25,
        bond_shock=-0.17,
        fx_shock=-0.07,
        recovery_index="S&P 500",
        recovery_months=24,
        recovery_note=(
            "The S&P 500 regained its January 2022 peak in about 24 months (January 2024)."
        ),
    ),
    Scenario(
        key="dotcom_2000",
        name="Dot-com Bust (2000-2002)",
        description=(
            "The tech bubble deflated over two and a half years. The S&P 500 "
            "fell about 49% (the NASDAQ nearly 78%) — a long grind, not a "
            "sudden crash."
        ),
        # S&P 500: 1,527 (Mar 2000) -> 777 (Oct 2002) = -49.1%.
        source="S&P 500, Mar 2000 - Oct 2002",
        equity_shock=-0.49,
        bond_shock=0.09,
        fx_shock=0.0,
        recovery_index="S&P 500",
        recovery_months=86,
        recovery_note="The S&P 500 took about 86 months (until 2007) to regain its 2000 peak.",
    ),
    Scenario(
        key="rates_up_200bp",
        name="Interest Rates +2%",
        description=(
            "A hypothetical sudden 2-percentage-point rise in rates. Bond "
            "prices fall roughly by their duration times the rate move "
            "(aggregate bond funds have ~6-year duration); equities typically "
            "de-rate too."
        ),
        source="Duration approximation: dP/P = -duration x d(yield); ~6y aggregate duration",
        equity_shock=-0.10,
        bond_shock=-0.12,
        fx_shock=0.0,
        recovery_index="—",
        recovery_months=None,
        recovery_note="Hypothetical scenario — no historical recovery to cite.",
    ),
    Scenario(
        key="oil_shock",
        name="Oil Supply Shock",
        description=(
            "A sudden oil-supply disruption (as in 1973 or 1990) squeezing "
            "growth: equities sell off while energy costs spike."
        ),
        source="1973 OPEC embargo, 1990 Gulf shock equity drawdowns (~-15%)",
        equity_shock=-0.15,
        bond_shock=0.0,
        fx_shock=0.03,  # CAD is a petro-currency; USD/CAD tends to fall (CAD strengthens)
        recovery_index="S&P 500",
        recovery_months=None,
        recovery_note="Historical oil-shock drawdowns recovered within roughly 1-2 years.",
    ),
    Scenario(
        key="cad_swing",
        name="CAD/USD ±15% Swing",
        description=(
            "Your holdings are priced in USD but your life is in CAD. A 15% "
            "currency move changes what your portfolio is worth in Canadian "
            "dollars even if the stocks themselves don't move."
        ),
        source="USD/CAD has moved >15% in 12 months multiple times (2008, 2015, 2020)",
        equity_shock=0.0,
        bond_shock=0.0,
        fx_shock=-0.15,
        recovery_index="—",
        recovery_months=None,
        recovery_note="Currency swings affect the CAD value only; USD figures are unchanged.",
    ),
)


def apply_scenario(
    scenario: Scenario,
    positions: list[dict],  # {symbol, market_value, beta (float|None), currency}
    reporting_currency: str = "CAD",
) -> dict:
    """Portfolio impact: equity shocked by equity_shock*beta, bonds by
    bond_shock, and foreign-currency positions additionally by fx_shock.

    ``market_value`` is in the reporting currency; ``currency`` is the
    position's own. Returns per-position and total impacts in that same
    reporting currency.
    """
    per_position = []
    total_value = 0.0
    total_impact = 0.0
    for p in positions:
        value = float(p["market_value"])
        total_value += value
        if p["symbol"].upper() in BOND_ETFS:
            market_shock = scenario.bond_shock
        else:
            b = p.get("beta")
            market_shock = scenario.equity_shock * (b if b is not None else 1.0)
        # A long position cannot lose more than its value, however high its beta.
        market_shock = max(market_shock, -1.0)

        # Currency risk only exists where the position is not already in the
        # currency the user is being reported in.
        native = str(p.get("currency") or reporting_currency).upper()
        fx = scenario.fx_shock if native != reporting_currency.upper() else 0.0
        shock = max((1.0 + market_shock) * (1.0 + fx) - 1.0, -1.0)

        impact = value * shock
        total_impact += impact
        per_position.append(
            {"symbol": p["symbol"], "impact": round(impact, 2), "shock_pct": round(shock, 4)}
        )
    return {
        "portfolio_impact": {
            "amount": round(total_impact, 2),
            "pct": round(total_impact / total_value, 4) if total_value else 0.0,
        },
        "per_position": per_position,
    }
