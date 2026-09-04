"""Multi-factor risk decomposition — pure numpy, no I/O.

A single equity beta answers "how much market?" and nothing else. This module
answers "how much of WHICH risk?" by regressing each holding's returns on a
small set of factor return series, then aggregating exposures to the portfolio
and splitting its variance across the factors.

Factors are represented by liquid **proxy ETFs**, not by reference data. That
is a deliberate trade: a duration or sector feed would be more precise for
bonds specifically, but it needs a licensed source and it silently gives up on
any ticker it does not cover. A regression on prices we already sync works for
every symbol in the book on day one.

Conventions follow ``invest_math``: A = 252 trading days/year, ddof=1 for
sample statistics, and metrics need >= MIN_DAYS overlapping days or they are
None.

The aggregation is correlation-aware. Factor risks are NOT added: the portfolio
factor variance is ``b' F b`` with the full factor covariance ``F``, so the
cross terms (rates-vs-FX being the one that bites a Canadian investor holding
US assets) are carried rather than assumed away. ``diversification_benefit``
reports what those cross terms are worth against a naive sum of standalone
risks.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.analytics.invest_math import MIN_DAYS, TRADING_DAYS

#: Effective duration (years) of the IR proxy, used to translate a regression
#: beta into an interpretable duration figure. iShares publishes ~7.5y for IEF.
IR_PROXY_DURATION_YEARS = 7.5


@dataclass(frozen=True)
class Factor:
    key: str
    name: str
    #: Proxy tickers. One symbol is used directly; two form a long-short
    #: spread (``long - short``) so the factor isolates the driver instead of
    #: re-importing the market move both legs share.
    proxies: tuple[str, ...]
    description: str
    unit: str | None = None


FACTORS: tuple[Factor, ...] = (
    Factor(
        key="equity",
        name="Equity market",
        proxies=("SPY",),
        description=(
            "Broad stock-market direction. Nearly every holding in a long-only "
            "book loads on this; it is the factor the old single-beta number "
            "measured."
        ),
    ),
    Factor(
        key="ir",
        name="Interest rate",
        proxies=("IEF",),
        description=(
            "Sensitivity to government bond yields, via 7-10y Treasuries. A "
            "positive exposure gains when rates FALL — that is what holding "
            "duration means."
        ),
        unit="duration-years",
    ),
    Factor(
        key="fx_usdcad",
        name="USD/CAD",
        proxies=("USDCAD=X",),
        description=(
            "Currency risk for a Canadian investor. A US-listed holding carries "
            "this whether or not you chose it: the stock can be flat in USD and "
            "still move your net worth in CAD."
        ),
    ),
    Factor(
        key="commodity",
        name="Commodity",
        proxies=("DBC",),
        description=(
            "Broad commodity prices — energy, metals, agriculture. Shows up "
            "through resource-heavy holdings, which is most of the TSX."
        ),
    ),
    Factor(
        key="credit",
        name="Credit spread",
        proxies=("HYG", "LQD"),
        description=(
            "The extra risk in lower-quality debt, as high-yield minus "
            "investment-grade. Widens in exactly the conditions that hurt "
            "equities, so it is where 'diversified' bond funds can disappoint."
        ),
    ),
)

FACTOR_BY_KEY = {f.key: f for f in FACTORS}

#: Every proxy ticker any factor needs, for the caller's price sync.
PROXY_SYMBOLS: tuple[str, ...] = tuple(dict.fromkeys(p for f in FACTORS for p in f.proxies))


@dataclass(frozen=True)
class FactorExposure:
    key: str
    name: str
    exposure: float
    #: Share of portfolio variance attributable to this factor (may be
    #: negative: a factor can hedge, and pretending otherwise would overstate
    #: total risk).
    variance_share: float
    contrib_vol: float
    standalone_vol: float
    unit: str | None
    duration_years: float | None = None


@dataclass(frozen=True)
class FactorRisk:
    exposures: list[FactorExposure]
    factor_vol: float
    idiosyncratic_vol: float
    total_vol: float
    r_squared: float
    diversification_benefit: float
    factors_used: list[str]
    factors_unavailable: list[str]


def to_reporting_currency(
    asset_returns: dict[str, np.ndarray],
    currency_by_symbol: dict[str, str | None],
    fx_returns: np.ndarray | None,
    reporting_currency: str = "CAD",
) -> dict[str, np.ndarray]:
    """Restate native-currency returns in the reporting currency.

    Without this the FX factor is close to meaningless: a US-listed holding
    quoted in USD shows almost no USD/CAD loading, because its own price series
    never contains the currency move. What a Canadian holder actually earns is
    ``(1+r_usd)(1+r_fx)-1``, and only that series carries the FX risk they bear.

    Symbols already in the reporting currency pass through. So does everything
    if no FX series is available — a silent half-conversion would be worse than
    an honest un-converted number.
    """
    if fx_returns is None:
        return dict(asset_returns)
    out: dict[str, np.ndarray] = {}
    for symbol, r in asset_returns.items():
        native = str(currency_by_symbol.get(symbol) or reporting_currency).upper()
        if native == reporting_currency.upper() or len(r) != len(fx_returns):
            out[symbol] = r
        else:
            out[symbol] = (1.0 + r) * (1.0 + fx_returns) - 1.0
    return out


def build_factor_returns(
    returns: dict[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], list[str]]:
    """Turn proxy return series into factor return series.

    Returns ``(factor_key -> returns, unavailable_factor_keys)``. A factor whose
    proxies are missing is dropped rather than faked — a zero series would read
    as "no exposure" instead of "not measured".
    """
    out: dict[str, np.ndarray] = {}
    unavailable: list[str] = []
    for factor in FACTORS:
        if not all(p in returns for p in factor.proxies):
            unavailable.append(factor.key)
            continue
        if len(factor.proxies) == 1:
            out[factor.key] = returns[factor.proxies[0]]
        else:
            long_leg, short_leg = factor.proxies[0], factor.proxies[1]
            out[factor.key] = returns[long_leg] - returns[short_leg]
    return out, unavailable


def fit_asset(
    asset_returns: np.ndarray, factor_matrix: np.ndarray
) -> tuple[np.ndarray, float] | None:
    """OLS of one asset on the factors: ``(betas, residual variance)``.

    ``factor_matrix`` is (n_obs, k). The intercept is fitted but not returned.
    Yields None when the system is too short or rank-deficient to solve
    honestly.

    The residual variance is the asset's own risk — what the factors did not
    explain. It is returned alongside the betas because a projection needs it:
    you cannot say what a proposed trade does to single-name risk from
    portfolio-level numbers alone.
    """
    n_obs, k = factor_matrix.shape
    if n_obs < MIN_DAYS or n_obs <= k + 1:
        return None
    # A holding whose series does not line up with the factor panel (a ticker
    # with a short or gappy history) is unsolvable, not fatal — the caller
    # redistributes its weight.
    if asset_returns.shape[0] != n_obs:
        return None
    design = np.column_stack([np.ones(n_obs), factor_matrix])
    if np.linalg.matrix_rank(design) < design.shape[1]:
        return None
    coeffs, *_ = np.linalg.lstsq(design, asset_returns, rcond=None)
    residual = asset_returns - design @ coeffs
    return coeffs[1:], float(np.var(residual, ddof=1))


def exposures_for(asset_returns: np.ndarray, factor_matrix: np.ndarray) -> np.ndarray | None:
    """OLS betas of one asset on the factors (intercept fitted, not returned)."""
    fit = fit_asset(asset_returns, factor_matrix)
    return None if fit is None else fit[0]


def decompose(
    weights: dict[str, float],
    asset_returns: dict[str, np.ndarray],
    factor_returns: dict[str, np.ndarray],
    unavailable: list[str] | None = None,
) -> FactorRisk | None:
    """Portfolio factor exposures and the variance each factor explains.

    ``weights`` must already be normalised across the assets present in
    ``asset_returns``. Exposures aggregate linearly (b_p = sum_i w_i b_i);
    variance does not, so it is computed as ``b' F b`` with the factor
    covariance matrix.
    """
    keys = [f.key for f in FACTORS if f.key in factor_returns]
    held = [s for s in weights if s in asset_returns]
    if not keys or not held:
        return None

    factor_matrix = np.column_stack([factor_returns[k] for k in keys])
    n_obs = factor_matrix.shape[0]
    if n_obs < MIN_DAYS:
        return None

    # Portfolio exposure is the weighted sum of per-asset exposures. An asset
    # the regression cannot solve is skipped and its weight redistributed, so
    # one illiquid holding does not void the whole decomposition.
    betas: list[np.ndarray] = []
    used_weights: list[float] = []
    used_symbols: list[str] = []
    for symbol in held:
        b = exposures_for(asset_returns[symbol], factor_matrix)
        if b is None:
            continue
        betas.append(b)
        used_weights.append(weights[symbol])
        used_symbols.append(symbol)
    if not betas:
        return None

    wsum = sum(used_weights)
    if wsum <= 0:
        return None
    w = np.array(used_weights) / wsum
    b_p = np.sum(np.array(betas) * w[:, None], axis=0)

    cov = np.cov(factor_matrix, rowvar=False, ddof=1)
    cov = np.atleast_2d(cov)

    # Portfolio return series under the same weights, for total and residual
    # variance. Uses the actual series rather than the fitted values so the
    # idiosyncratic piece is what the factors genuinely failed to explain.
    r_p = np.zeros(n_obs)
    for weight, symbol in zip(w, used_symbols, strict=True):
        r_p = r_p + weight * asset_returns[symbol]

    total_var = float(np.var(r_p, ddof=1))
    factor_var = float(b_p @ cov @ b_p)
    # Clamp: sampling noise can push the explained piece just past the total.
    factor_var = max(0.0, min(factor_var, total_var)) if total_var > 0 else 0.0
    idio_var = max(0.0, total_var - factor_var)

    ann = float(TRADING_DAYS)
    contributions = b_p * (cov @ b_p)  # sums to b' F b
    factor_vol = float(np.sqrt(factor_var * ann))
    total_vol = float(np.sqrt(total_var * ann)) if total_var > 0 else 0.0

    exposures: list[FactorExposure] = []
    standalone_total = 0.0
    for i, key in enumerate(keys):
        factor = FACTOR_BY_KEY[key]
        share = float(contributions[i] / factor_var) if factor_var > 0 else 0.0
        # Signed: a hedging factor reduces vol, and squaring that away would
        # hide the hedge.
        contrib_vol = float(np.sign(contributions[i]) * np.sqrt(abs(contributions[i]) * ann))
        standalone = float(abs(b_p[i]) * np.sqrt(cov[i, i] * ann))
        standalone_total += standalone
        exposures.append(
            FactorExposure(
                key=key,
                name=factor.name,
                exposure=float(b_p[i]),
                variance_share=share,
                contrib_vol=contrib_vol,
                standalone_vol=standalone,
                unit=factor.unit,
                duration_years=(float(b_p[i] * IR_PROXY_DURATION_YEARS) if key == "ir" else None),
            )
        )

    return FactorRisk(
        exposures=exposures,
        factor_vol=factor_vol,
        idiosyncratic_vol=float(np.sqrt(idio_var * ann)),
        total_vol=total_vol,
        r_squared=float(factor_var / total_var) if total_var > 0 else 0.0,
        diversification_benefit=factor_vol - standalone_total,
        factors_used=keys,
        factors_unavailable=list(unavailable or []),
    )


# --- projection --------------------------------------------------------------
#
# `decompose` answers "where does today's risk come from". A guardrail needs a
# second question answered: "where would it come from if I placed these trades".
# That cannot be read off a portfolio-level result — exposures aggregate from
# the per-asset betas, so a projection needs the betas themselves plus each
# asset's own residual risk. `FactorModel` is that fitted object, and `project`
# evaluates it at any set of weights.


@dataclass(frozen=True)
class FactorState:
    """Where a book sits under one set of weights.

    Weights are NOT renormalised: they are shares of whatever denominator the
    caller chose, and anything not named simply carries no factor risk. That is
    what makes cash meaningful here — a book half in cash really does bear half
    the currency risk, and a projection that renormalised it away would report
    that spending the cash changed nothing.
    """

    exposures: dict[str, float]
    #: Share of *factor* variance, matching :class:`FactorExposure`, so the
    #: number a limit is checked against is the number the user was shown.
    variance_share: dict[str, float]
    factor_vol: float
    idiosyncratic_vol: float
    total_vol: float
    duration_years: float | None
    #: Fraction of the passed-in weight the model could actually price. Thin
    #: coverage is a reason to decline to enforce anything, not to enforce
    #: against a number built from a third of the book.
    covered_weight: float

    @property
    def idiosyncratic_share(self) -> float:
        """Single-name risk as a fraction of total risk."""
        return self.idiosyncratic_vol / self.total_vol if self.total_vol > 0 else 0.0


@dataclass(frozen=True)
class FactorModel:
    """Per-asset betas and residual risks, fitted once and evaluated many times."""

    keys: list[str]
    cov: np.ndarray  # (k, k) daily factor covariance
    betas: dict[str, np.ndarray]
    residual_var: dict[str, float]
    factors_unavailable: list[str]

    def covers(self, symbol: str) -> bool:
        return symbol in self.betas

    def beta(self, symbol: str, key: str) -> float | None:
        b = self.betas.get(symbol)
        return None if b is None or key not in self.keys else float(b[self.keys.index(key)])

    def project(self, weights: dict[str, float]) -> FactorState | None:
        if not self.keys or not self.betas:
            return None
        total_weight = sum(abs(w) for w in weights.values())
        priced = {s: w for s, w in weights.items() if s in self.betas}
        if not priced:
            return None

        b_p = np.zeros(len(self.keys))
        idio_var = 0.0
        for symbol, weight in priced.items():
            b_p = b_p + weight * self.betas[symbol]
            idio_var += weight * weight * self.residual_var.get(symbol, 0.0)

        # Residuals are treated as mutually uncorrelated — the standard factor
        # assumption, and the only one available: what two holdings' leftovers
        # do together is by construction not something the factors saw.
        factor_var = max(0.0, float(b_p @ self.cov @ b_p))
        total_var = factor_var + idio_var
        ann = float(TRADING_DAYS)
        contributions = b_p * (self.cov @ b_p)

        exposures = {k: float(b_p[i]) for i, k in enumerate(self.keys)}
        shares = {
            k: (float(contributions[i] / factor_var) if factor_var > 0 else 0.0)
            for i, k in enumerate(self.keys)
        }
        ir = exposures.get("ir")
        return FactorState(
            exposures=exposures,
            variance_share=shares,
            factor_vol=float(np.sqrt(factor_var * ann)),
            idiosyncratic_vol=float(np.sqrt(idio_var * ann)),
            total_vol=float(np.sqrt(total_var * ann)),
            duration_years=(ir * IR_PROXY_DURATION_YEARS if ir is not None else None),
            covered_weight=(
                sum(abs(w) for w in priced.values()) / total_weight if total_weight > 0 else 0.0
            ),
        )


def fit_model(
    symbols: list[str],
    asset_returns: dict[str, np.ndarray],
    factor_returns: dict[str, np.ndarray],
    unavailable: list[str] | None = None,
) -> FactorModel | None:
    """Regress every symbol on the factor panel and keep what solved.

    A symbol the regression cannot solve is left out of ``betas`` rather than
    given zeros: no exposure and not measured are different claims, and only
    the caller knows whether the gap is small enough to proceed on.
    """
    keys = [f.key for f in FACTORS if f.key in factor_returns]
    if not keys:
        return None
    factor_matrix = np.column_stack([factor_returns[k] for k in keys])
    if factor_matrix.shape[0] < MIN_DAYS:
        return None

    betas: dict[str, np.ndarray] = {}
    residual_var: dict[str, float] = {}
    for symbol in dict.fromkeys(symbols):
        series = asset_returns.get(symbol)
        if series is None:
            continue
        fit = fit_asset(series, factor_matrix)
        if fit is None:
            continue
        betas[symbol], residual_var[symbol] = fit
    if not betas:
        return None

    return FactorModel(
        keys=keys,
        cov=np.atleast_2d(np.cov(factor_matrix, rowvar=False, ddof=1)),
        betas=betas,
        residual_var=residual_var,
        factors_unavailable=list(unavailable or []),
    )
