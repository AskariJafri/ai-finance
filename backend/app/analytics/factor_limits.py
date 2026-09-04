"""Deterministic limits on where a plan's risk is allowed to come from.

The committee reasons about factor exposure because it is put in front of it,
and reasoning is not enforcement: a prompt can be ignored on any given run, and
the runs that ignore it are exactly the ones nobody notices. So the factor block
does two jobs, separately. It goes into the prompt as context, and it comes back
out here as a rule the app applies itself — the same split concentration already
has, where ``CONCENTRATION_CAP`` fires whatever the model thought.

**These limits may only ever decline to buy.** Nothing here can size a sell.
A metric being over its line is not a reason to liquidate: the same principle
that stops a concentration trim when the proceeds have nowhere to go applies
with more force to a number this indirect, and a guardrail that could force
selling would turn a modelling artifact into a taxable event. A breach the plan
did not cause is reported, not acted on.

Everything is pure — a fitted :class:`~app.analytics.factor_risk.FactorModel`
and a dict of amounts in, decisions out — so the policy is testable without a
database, a provider or a model call.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analytics.decision_context import FactorLine, PortfolioBrief
from app.analytics.factor_risk import FACTOR_BY_KEY, FactorModel, FactorState

#: A factor moving less than this share of the portfolio's risk is left out of
#: the prompt. Listing every factor at 0% invites the model to comment on all of
#: them; the point is the two or three that actually drive the book.
MATERIAL_SHARE = 0.02

#: How much of the book the model must be able to price before any limit is
#: enforced. Below this the exposures are a statement about a minority of the
#: money, and blocking a trade on them would be worse than not checking.
MIN_COVERAGE = 0.6

#: Fractions of the proposed buys that are tried, largest first. A grid rather
#: than a bisection because the variance-share measures are not monotone in the
#: trade size — a search that assumed they were could cut a buy that was never
#: the problem.
_SCALES = tuple(i / 20 for i in range(20, -1, -1))


@dataclass(frozen=True)
class Limits:
    """The lines themselves. Defaults live in settings, not here."""

    fx_ceiling: float = 0.70
    share_ceiling: float = 0.85
    idio_ceiling: float = 0.70
    duration_min: float = -2.0
    duration_max: float = 8.0


@dataclass(frozen=True)
class FactorBreach:
    key: str
    name: str
    #: Where the book sits today, and where the plan would leave it. Both are
    #: measured through the same projection, so the difference between them is
    #: the trades and nothing else.
    current: float
    projected: float
    limit: float
    unit: str  # exposure | share | years
    #: True when the plan was actually cut back. False means the limit is over
    #: its line for reasons this plan did not create and cannot fix by buying.
    binding: bool
    message: str


def _fmt(value: float, unit: str) -> str:
    if unit == "share":
        return f"{value:.0%}"
    if unit == "years":
        return f"{value:+.1f} yrs"
    return f"{value:.2f}"


class _Limit:
    """One line, and how far past it a state sits.

    ``binds`` is the difference between a cap and a flag, and it is not a
    matter of severity — it is a matter of what buying can do about it.

    An exposure LEVEL (currency, duration) is something a buy adds to, so
    declining to buy is a real remedy: less of a US name is less currency risk.
    A SHARE of risk is a ratio, and deploying cash into anything at all moves
    its denominator. On a book whose risk is already 91% equity, treating that
    ratio as a cap does not produce a more balanced plan — it produces no plan,
    because every equity purchase nudges the ratio up and no smaller purchase
    ever clears the line. The user is then told to stay in cash, which is not
    diversification and was never the advice anyone wanted.

    So ratios report and levels enforce. The ratio is still worth saying out
    loud — it is the one number that catches "I own eight things" hiding one
    bet — but the thing it should change is what you buy NEXT, and that is a
    decision for the person, informed, not one for a scaling loop.
    """

    key: str
    name: str
    unit: str
    binds: bool = True

    def value(self, state: FactorState) -> float | None:
        raise NotImplementedError

    def bound(self, limits: Limits) -> float:
        raise NotImplementedError

    def violation(self, state: FactorState, limits: Limits) -> float:
        """How far outside the limit, in the metric's own units. 0 when inside."""
        v = self.value(state)
        return 0.0 if v is None else max(0.0, v - self.bound(limits))

    def sentence(self, current: float, projected: float, limits: Limits) -> str:
        raise NotImplementedError


class _FxCeiling(_Limit):
    key, name, unit = "fx_usdcad", "USD/CAD exposure", "exposure"

    def value(self, state: FactorState) -> float | None:
        return state.exposures.get("fx_usdcad")

    def bound(self, limits: Limits) -> float:
        return limits.fx_ceiling

    def sentence(self, current: float, projected: float, limits: Limits) -> str:
        return (
            "buying more US-denominated names would lift USD/CAD exposure from "
            f"{current:.2f} to {projected:.2f}, past the {limits.fx_ceiling:.2f} ceiling. "
            "A US holding carries the currency whether or not you chose it."
        )


class _EquityShare(_Limit):
    key, name, unit = "equity_share", "Equity share of risk", "share"
    binds = False  # a ratio: see _Limit.binds

    def value(self, state: FactorState) -> float | None:
        return state.variance_share.get("equity")

    def bound(self, limits: Limits) -> float:
        return limits.share_ceiling

    def sentence(self, current: float, projected: float, limits: Limits) -> str:
        return (
            f"the stock market already explains {current:.0%} of this portfolio's risk, "
            f"and these buys would take it to {projected:.0%} — past the "
            f"{limits.share_ceiling:.0%} limit. Holding more names is not "
            "diversification when they all answer to the same factor."
        )


class _IdiosyncraticCeiling(_Limit):
    key, name, unit = "idiosyncratic", "Single-name risk", "share"
    binds = False  # a ratio: see _Limit.binds

    def value(self, state: FactorState) -> float | None:
        return state.idiosyncratic_share

    def bound(self, limits: Limits) -> float:
        return limits.idio_ceiling

    def sentence(self, current: float, projected: float, limits: Limits) -> str:
        return (
            "risk specific to individual holdings — the part no factor explains — would go "
            f"from {current:.0%} to {projected:.0%} of total risk, past the "
            f"{limits.idio_ceiling:.0%} limit. That is the risk nobody is paid to take."
        )


class _DurationBand(_Limit):
    key, name, unit = "duration", "Implied duration", "years"

    def value(self, state: FactorState) -> float | None:
        return state.duration_years

    def bound(self, limits: Limits) -> float:
        return limits.duration_max

    def violation(self, state: FactorState, limits: Limits) -> float:
        v = self.value(state)
        if v is None:
            return 0.0
        return max(0.0, limits.duration_min - v, v - limits.duration_max)

    def sentence(self, current: float, projected: float, limits: Limits) -> str:
        direction = "long" if projected > limits.duration_max else "short"
        return (
            f"implied duration would move from {current:+.1f} to {projected:+.1f} years, "
            f"outside the {limits.duration_min:+.1f} to {limits.duration_max:+.1f} band — "
            f"a {direction} bet on interest rates, taken as a side effect."
        )


#: Order matters: each limit is applied to the buys the previous one left, so a
#: fixed order is what makes the outcome reproducible.
LIMITS: tuple[_Limit, ...] = (
    _FxCeiling(),
    _DurationBand(),
    _EquityShare(),
    _IdiosyncraticCeiling(),
)


@dataclass(frozen=True)
class Constrained:
    """What the overlay decided."""

    buys: dict[str, float]
    breaches: list[FactorBreach]
    state_now: FactorState | None
    state_after: FactorState | None

    @property
    def enforced(self) -> bool:
        return any(b.binding for b in self.breaches)


def brief(state: FactorState | None, limits: Limits) -> PortfolioBrief | None:
    """The same state, shaped for the committee prompt.

    Deliberately built from the object the overlay checks, rather than from a
    parallel formatting of the risk endpoint's numbers. If what the seats are
    told and what the app enforces could drift apart, they eventually would —
    and the run where they disagreed would look exactly like a run where the
    model ignored its instructions.
    """
    if state is None:
        return None
    lines = tuple(
        FactorLine(
            name=FACTOR_BY_KEY[key].name if key in FACTOR_BY_KEY else key,
            exposure=round(state.exposures.get(key, 0.0), 4),
            share_of_risk=round(share, 4),
            detail=(
                f"{state.duration_years:+.1f} yrs"
                if key == "ir" and state.duration_years is not None
                else None
            ),
        )
        for key, share in state.variance_share.items()
        if abs(share) >= MATERIAL_SHARE
    )
    if not lines:
        return None
    over = tuple(limit.name for limit in LIMITS if limit.violation(state, limits) > 0)
    return PortfolioBrief(
        lines=lines,
        idiosyncratic_share=round(state.idiosyncratic_share, 4),
        total_vol=round(state.total_vol, 4) if state.total_vol else None,
        over=over,
    )


def _weights(values: dict[str, float], equity: float) -> dict[str, float]:
    return {s: v / equity for s, v in values.items()} if equity > 0 else {}


def _worsens(limit: _Limit, before: FactorState, after: FactorState, limits: Limits) -> bool:
    return limit.violation(after, limits) > limit.violation(before, limits) + 1e-9


def constrain(
    model: FactorModel | None,
    *,
    equity: float,
    positions: dict[str, float],
    buys: dict[str, float],
    limits: Limits,
) -> Constrained:
    """Cut back the buys until the plan sits inside every limit it can.

    ``positions`` is the book's market value per symbol *after* the plan's
    sells; ``buys`` is CAD to spend per symbol. Both are measured against
    ``equity`` — total account value, cash included — because that is the
    denominator every other weight in a plan uses, and because cash genuinely
    dilutes factor risk: a limit that renormalised it away would report that
    spending it changed nothing.
    """
    unchanged = Constrained(buys=dict(buys), breaches=[], state_now=None, state_after=None)
    if model is None or equity <= 0:
        return unchanged

    state_now = model.project(_weights(positions, equity))
    if state_now is None or state_now.covered_weight < MIN_COVERAGE:
        # Not enough of the book is priced for these numbers to mean anything.
        # Reporting a breach here would be the model's coverage gap wearing the
        # costume of a portfolio problem.
        return unchanged

    def project(scaled: dict[str, float]) -> FactorState | None:
        combined = dict(positions)
        for symbol, amount in scaled.items():
            combined[symbol] = combined.get(symbol, 0.0) + amount
        return model.project(_weights(combined, equity))

    working = dict(buys)
    breaches: list[FactorBreach] = []

    for limit in LIMITS:
        after = project(working)
        if after is None or limit.violation(after, limits) <= 0:
            continue

        current, projected = limit.value(state_now), limit.value(after)
        if current is None or projected is None:
            continue
        bound = limit.bound(limits)

        # A limit the plan did not push past is not this plan's to fix. Buying
        # less cannot repair an exposure the existing holdings already carry,
        # and cutting trades to look like it did would be theatre. An advisory
        # limit takes this exit unconditionally: it is here to be said, not
        # applied.
        if not limit.binds or (
            limit.violation(state_now, limits) > 0 and not _worsens(limit, state_now, after, limits)
        ):
            if _worsens(limit, state_now, after, limits):
                # The plan does push this further, and the app still lets it.
                # Say both halves plainly: what moves, and that nothing was
                # stopped — a warning dressed as an enforcement is worse than
                # either one on its own.
                message = (
                    limit.sentence(current, projected, limits)
                    + " No order was changed: deploying cash into anything moves this "
                    "ratio, so capping it would only ever mean staying in cash. "
                    "It is the balance to fix with what you buy next."
                )
            else:
                message = (
                    f"{limit.name} is already at {_fmt(current, limit.unit)}, past the "
                    f"{_fmt(bound, limit.unit)} limit. This plan does not make it worse, "
                    "and cutting these buys would not bring it back — only a holding "
                    "that answers to something else would. Worth knowing before the "
                    "next decision."
                )
            breaches.append(
                FactorBreach(
                    key=limit.key,
                    name=limit.name,
                    current=round(current, 4),
                    projected=round(projected, 4),
                    limit=round(bound, 4),
                    unit=limit.unit,
                    binding=False,
                    message=message,
                )
            )
            continue

        # Which buys are the problem: the ones whose removal actually helps.
        # Asking the projection rather than reasoning from the betas keeps this
        # right for the variance-share limits, where a name can push one measure
        # up by pulling another down.
        worst = limit.violation(after, limits)
        contributors = [
            symbol
            for symbol in working
            if (state := project({s: a for s, a in working.items() if s != symbol})) is not None
            and limit.violation(state, limits) < worst
        ]
        if not contributors:
            breaches.append(
                FactorBreach(
                    key=limit.key,
                    name=limit.name,
                    current=round(current, 4),
                    projected=round(projected, 4),
                    limit=round(bound, 4),
                    unit=limit.unit,
                    binding=False,
                    message=(
                        f"{limit.name} would reach {_fmt(projected, limit.unit)}, past the "
                        f"{_fmt(bound, limit.unit)} limit — but none of these buys is what "
                        "puts it there, so none was cut."
                    ),
                )
            )
            continue

        # Aim for the line, or for where the book already stands if it started
        # past it: "no worse than before" is the strongest thing a buy can
        # honestly promise, and a search for a zero that no trade size reaches
        # would run the grid to the bottom and cancel the order over a problem
        # it did not cause. For the level limits shipped today the two targets
        # coincide, since exposure is linear in the trade — this states the
        # invariant rather than waiting for a limit that breaks it.
        target = limit.violation(state_now, limits)
        chosen = 0.0
        for scale in _SCALES:
            trial = {s: (a * scale if s in contributors else a) for s, a in working.items()}
            state = project(trial)
            if state is not None and limit.violation(state, limits) <= target + 1e-9:
                chosen = scale
                break
        working = {s: (a * chosen if s in contributors else a) for s, a in working.items()}
        breaches.append(
            FactorBreach(
                key=limit.key,
                name=limit.name,
                current=round(current, 4),
                projected=round(projected, 4),
                limit=round(bound, 4),
                unit=limit.unit,
                binding=True,
                message=(
                    limit.sentence(current, projected, limits)
                    + (" Cut back: " if chosen else " Dropped: ")
                    + ", ".join(sorted(contributors))
                    + "."
                ),
            )
        )

    return Constrained(
        buys=working,
        breaches=breaches,
        state_now=state_now,
        state_after=project(working),
    )
