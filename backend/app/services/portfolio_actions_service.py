"""One plan covering both sides of the account: what to sell, what to buy.

The other planners each answer half the question. ``DeployService`` spends new
cash on names you do not own; ``propose_rebalance`` moves you toward target
weights you set by hand; the committee judges one ticker with no idea what else
you hold. Nothing decided what to do about a position you already own in light
of the market — which is the question people actually ask.

The shape mirrors ``DeployService``, and for the same reason: committee calls
are metered per day, so the work is staged.

  1. Every **held** position faces the committee — you cannot decide what to
     sell without judging what you own, and there are only ever a handful.
  2. Unheld candidates are ranked for free on cached bars, and only the top few
     get a committee call.
  3. The verdicts are turned into orders sized against one budget: cash on hand
     plus whatever the sells raise.

A held position is asked a different question than a candidate. The committee's
usual verdict is about *opening* a position, and "I would not buy this today" is
true of most things most of the time — reading that as "sell what you own"
produces a standing recommendation to liquidate. So held positions get their own
vocabulary (TRIM / HOLD / ADD) and their own instruction that selling clears a
higher bar than declining to buy, with the position's size and unrealized return
in front of the seats.

Guardrails carry over from the rest of the module. Every figure comes from
market data and the recorded holdings — the model interprets, it never derives a
number. Nothing here may set APPROVED: ``propose`` persists nothing at all, and
``accept`` writes PROPOSED rows the user still has to approve one by one.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.committee import HeldContext, run_committee_many, source_of
from app.ai.llm.base import LLMProvider
from app.ai.llm.budget import llm_budget
from app.analytics import factor_limits, factor_risk
from app.analytics.decision_context import (
    MacroBrief,
    MarketContext,
    PortfolioBrief,
    SymbolNews,
)
from app.analytics.indicators import Indicators, compute_indicators
from app.analytics.quant_signals import (
    EXIT_SCORE,
    alpha_weight,
    is_broad_fund,
    reconcile,
    vol_capped_weight,
)
from app.core.config import settings
from app.core.crypto import TokenCipher
from app.core.logging import get_logger
from app.db.models import ProposedOrder
from app.domain.enums import OrderSide
from app.integrations.marketdata.provider import MarketDataProvider
from app.integrations.snaptrade.gateway import SnapTradeGateway
from app.repositories.action_runs import VerdictCacheRepository, market_period
from app.repositories.holdings import HoldingRepository
from app.repositories.proposed_orders import ProposedOrderRepository
from app.schemas.common import cents_to_dollars
from app.schemas.invest import (
    ActionAcceptIn,
    ActionHoldOut,
    ActionOut,
    ActionPlanIn,
    ActionPlanOut,
    DeployPassedOut,
    FactorBreachOut,
    FactorPlanOut,
    PlanFactorOut,
    ProposalOut,
    Sizing,
)
from app.services.autotrader_service import rules_verdict
from app.services.deploy_service import CONCENTRATION_CAP, DeployService
from app.services.errors import ValidationFailed
from app.services.invest_service import InvestService, order_out
from app.services.live_quotes import broker_quote_fetcher
from app.services.market_context_service import MarketContextService, research_out
from app.services.market_data_service import FX_SYMBOL, MarketDataService

logger = get_logger(__name__)

# Below this a trade is not worth placing: the spread and the effort cost more
# than the drift it corrects. Wealthsimple will fill a $1 fractional order, so
# this is about usefulness rather than a broker limit.
MIN_TRADE_CAD = 20.0

# How much of a position a SKIP verdict sells. A confident "I would not own
# this" closes it; a tentative one halves it, because the committee being
# unsure is itself a reason not to act on the whole position at once.
FULL_EXIT_CONFIDENCE = 0.75
PARTIAL_TRIM_FRACTION = 0.5
# A trim the committee wanted but the signal only called a hold: a smaller
# step, since the two disagree about how urgent it is.
OVERLAY_TRIM_FRACTION = 1 / 3

# Rebalancing band. A held name is trimmed back to its risk-based ceiling only
# once it has drifted this far past it (25% relative, the "5/25" threshold
# rule), so normal day-to-day drift never generates a trade.
REBALANCE_BAND = 1.25

# Cash kept back rather than deployed to the last cent — an account with no cash
# at all cannot act on anything next month without selling first.
CASH_FLOOR_PCT = 0.02
MAX_RESERVE_SHARE = 0.25

# A buy should move the needle. Spreading a small budget evenly over every name
# the committee likes produces a page of $20 orders that change nothing and cost
# an afternoon to place — so the number of buys is bounded by what the budget
# can fund at this size, and conviction decides who makes the cut.
MEANINGFUL_TRADE_PCT = 0.03

# Below this, a buy coming in under its slice is reconciliation rather than a
# shortfall: allocations are rounded to cents individually, so the slices can
# sum to a few cents more than the budget they were carved from, and the last
# name absorbs it. Well under MIN_TRADE_CAD, so nothing that changes what the
# user actually does can hide beneath it.
FUNDING_SHORTFALL_CAD = 1.0

# How much price history the factor regressions get. One year, matching the risk
# screen's default window, so the exposures a plan is checked against are the
# same ones the user can go and look at.
FACTOR_WINDOW_DAYS = 365


def _limits() -> factor_limits.Limits:
    return factor_limits.Limits(
        fx_ceiling=settings.factor_fx_ceiling,
        share_ceiling=settings.factor_share_ceiling,
        idio_ceiling=settings.factor_idio_ceiling,
        duration_min=settings.factor_duration_min,
        duration_max=settings.factor_duration_max,
    )


def completed_closes(bars: list[tuple[date, float]], period: date) -> list[float]:
    """Closes from sessions that finished before ``period``.

    Today's bar is a live snapshot while the market is open. Feeding it to the
    signal made the verdict at 10am differ from the one at 2pm for no reason
    a person would recognise. Falls back to every bar when that would leave
    nothing (a listing whose only data is today's).
    """
    done = [c for d, c in bars if d < period]
    return done or [c for _, c in bars]


def anchor_to_signal(result: dict, ind: Indicators | None, *, held: bool) -> dict:
    """Fix a committee verdict to the house signal, within one notch.

    The signal is computed from completed closes, so it is the same all day.
    The committee's verdict stands when it is within one step of it (a hold
    the signal calls an add, say); further than that, or against a hard rule
    like the trailing stop, the signal's side wins and the summary says why.
    A rules fallback simply becomes the signal's verdict.
    """
    signal = ind.signal if ind is not None else None
    if signal is None:
        return result
    symbol = str(result.get("symbol") or "")
    quant = signal.decide(held=held, symbol=symbol)
    said = str(result.get("action") or "").upper()
    ai_conf = float(result.get("confidence") or 0.5)
    summary = str(result.get("summary") or "").strip()

    if result.get("source") == "ai":
        final, why = reconcile(quant, said, signal, held=held, symbol=symbol)
    else:
        final, why = quant, None

    if why:
        confidence = signal.confidence(final)
        summary = (
            f"{final.title()} — {why}. The committee said {said.lower()}: {summary}"
            if summary
            else f"{final.title()} — {why}."
        )
    elif final == quant:
        # Two independent reads agreeing: average them.
        confidence = round((signal.confidence(final) + ai_conf) / 2, 2)
    else:
        # The committee's one-step adjustment. Never more sure than the
        # signal would be of its own call — they disagree.
        confidence = round(min(ai_conf, 0.7), 2)
    if result.get("source") != "ai":
        summary = f"Rules-based read ({signal.line()}); AI offline."

    return {
        **result,
        "action": final,
        "confidence": confidence,
        "summary": summary[:500],
        "signal": signal.as_dict(),
        "signal_line": signal.line(),
        "signal_action": quant,
        "committee_action": said,
    }


def _with_signals(
    actions: list[ActionOut], holds: list[ActionHoldOut], verdicts: list[dict]
) -> tuple[list[ActionOut], list[ActionHoldOut]]:
    """Stamp each line of the plan with the signal it was anchored to."""
    by_symbol = {
        v["symbol"]: {
            "signal_score": (v.get("signal") or {}).get("score"),
            "signal": v.get("signal_line"),
        }
        for v in verdicts
        if v.get("signal")
    }
    return (
        [a.model_copy(update=by_symbol.get(a.symbol, {})) for a in actions],
        [h.model_copy(update=by_symbol.get(h.symbol, {})) for h in holds],
    )


def _with_prices(
    actions: list[ActionOut], meta: dict[str, tuple[str | None, str | None]]
) -> list[ActionOut]:
    """Stamp each order with its price's source and time.

    A buy is priced off the live quote. A sell is priced off the position's
    value, which the plan refreshed from today's market data when it started —
    it is kept that way so a sell can never be sized for more shares than are
    held (see ``_sell_action``).
    """
    now = datetime.now(UTC).isoformat()
    out = []
    for a in actions:
        if a.side == "buy" and a.symbol in meta:
            source, as_of = meta[a.symbol]
        else:
            source, as_of = "position value", now
        out.append(a.model_copy(update={"price_source": source, "price_as_of": as_of}))
    return out


def _signal_terms(result: dict) -> dict:
    """The signal's score and volatility from an anchored verdict, if any."""
    sig = result.get("signal")
    if not isinstance(sig, dict):
        return {}
    return {"score": sig.get("score"), "vol": sig.get("vol")}


def trim_fraction(result: dict, confidence: float) -> float:
    """How much of a holding a TRIM sells.

    With the signal: a full exit when the trailing stop is hit or the trend is
    deeply negative; half when the signal itself calls a trim; a third when
    only the committee does. Without it, the committee's confidence decides.
    """
    sig = result.get("signal")
    if not isinstance(sig, dict):
        return 1.0 if confidence >= FULL_EXIT_CONFIDENCE else PARTIAL_TRIM_FRACTION
    if result.get("signal_action") != "TRIM":
        return OVERLAY_TRIM_FRACTION
    symbol = str(result.get("symbol") or "")
    if not is_broad_fund(symbol) and (
        sig.get("stop_hit") or float(sig.get("score") or 0) <= EXIT_SCORE
    ):
        return 1.0
    return PARTIAL_TRIM_FRACTION


@dataclass
class _Sized:
    """What stage 3 produced. A dataclass rather than a tuple because the
    factor overlay made it the fifth thing to unpack, and the fifth element of
    a tuple is the one that gets bound to the wrong name."""

    actions: list[ActionOut] = field(default_factory=list)
    holds: list[ActionHoldOut] = field(default_factory=list)
    cash_after: float = 0.0
    unfunded: bool = False
    factors: FactorPlanOut | None = None


@dataclass
class _Stage:
    """A judging stage with its database work done and its committee call not
    yet made. Split so the two stages' LLM fan-outs can run concurrently while
    everything touching the shared session stays sequential."""

    items: list[tuple[str, dict, Indicators]]
    options: dict
    #: Turns the committee's results into what the stage returns.
    finish: Callable[[list[dict]], tuple]
    #: Today's verdicts already on file, by symbol — not asked again.
    cached: dict[str, dict] = field(default_factory=dict)
    held: bool = False

    @classmethod
    def done(cls, value: tuple) -> _Stage:
        """A stage with nothing to ask — its answer is already known."""
        return cls(items=[], options={}, finish=lambda _results: value)


class PortfolioActionsService:
    def __init__(
        self,
        session: AsyncSession,
        provider: MarketDataProvider,
        llm: LLMProvider,
        context: MarketContextService | None = None,
        *,
        broker: SnapTradeGateway | None = None,
        cipher: TokenCipher | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.llm = llm
        # Optional keyword so every existing construction keeps working.
        self.context = context
        # With both set, prices come from the user's own brokerage in real
        # time where it can quote them (see live_quotes).
        self.broker = broker
        self.cipher = cipher
        self.market_data = MarketDataService(session, provider)
        self.holdings = HoldingRepository(session)
        self.proposals = ProposedOrderRepository(session)
        self.invest = InvestService(session, provider)
        # Candidate ranking, quoting and the scan universe are the cash
        # planner's problem already solved; this composes it rather than
        # growing a second copy that drifts out of step.
        self.deploy = DeployService(session, provider, llm, context)

    # --- planning (read-only: persists nothing) ---
    async def plan(self, user_id: uuid.UUID, body: ActionPlanIn) -> ActionPlanOut:
        rows = await self.holdings.list_for_user(user_id)
        if not rows:
            raise ValidationFailed(
                "Add your holdings — or sync your linked brokerage — before asking "
                "what to buy or sell."
            )

        # The trading day this plan is for. Signals are computed from sessions
        # completed before it and committee verdicts are pinned to it, so the
        # same question asked twice today gets the same answer.
        self._period = market_period()
        self._user_id = user_id
        self._quote_meta: dict[str, tuple[str | None, str | None]] = {}

        # Price the book before valuing it: a position synced from the broker
        # minutes ago has no cached bars yet, and an unpriced holding would drop
        # out of the portfolio silently — taking any decision about it with it.
        # refresh_today: value the book at today's price as of now, not at
        # whatever the first sync of the day saw. The signals ignore today's
        # bar either way (completed sessions only), so this moves the money
        # figures without moving the verdicts.
        await self.market_data.sync_bars([h.symbol for h in rows], refresh_today=True)
        await self.session.commit()

        # Real-time quotes from the user's brokerage when they have one linked;
        # market data otherwise. Resolved once, used by every quote below.
        self.deploy.live_quotes = await broker_quote_fetcher(
            self.session, user_id, self.broker, self.cipher
        )

        portfolio = await self.invest.portfolio(user_id)
        if not portfolio.connected or not portfolio.positions:
            raise ValidationFailed(
                "Couldn't price any of your holdings yet. Try again once market data has synced."
            )

        cash = (portfolio.account.cash if portfolio.account else 0.0) + body.added_cash
        equity = (portfolio.account.equity if portfolio.account else 0.0) + body.added_cash

        # `portfolio()` weights each position against *invested* value. Every
        # weight in a plan is against total equity instead, because that is what
        # the concentration limit means and what the projected weights compare
        # to — mixing the two makes "20.6% -> 6.3%" a comparison of two
        # different denominators.
        held = {
            p["symbol"]: {**p, "weight": round(p["market_value"] / equity, 4) if equity else 0.0}
            for p in portfolio.positions
        }

        passed: list[DeployPassedOut] = []
        use_ai = body.use_ai and settings.ai_available and await self.deploy._llm_ready()

        # What KIND of risk this book already carries, before anything is judged.
        # Fitted on the holdings alone so the seats see it from the first call;
        # refitted below over the candidates too, because a projection can only
        # compare a book to itself when everything in it was measured the same
        # way. The seats get the picture; the overlay gets the arithmetic.
        currencies = {p["symbol"]: p.get("currency") for p in portfolio.positions}
        model = await self._fit_factors(currencies)
        limits = _limits()
        brief = factor_limits.brief(
            model.project({s: held[s]["weight"] for s in held}) if model else None, limits
        )

        # One macro read for the whole plan. Both stages share it rather than
        # each hitting the cache, and it is the same row every other user gets
        # today — see MarketContextService.macro.
        macro = None
        if use_ai and self.context is not None:
            macro = await self.context.macro()
            await self.session.commit()

        # Both stages append into one dict, the same way they already append
        # into `passed`, so the response can report the research as a whole.
        researched: dict[str, SymbolNews] = {}
        # Both stages' database and market-data work runs first, in order —
        # they share one AsyncSession, which two tasks must not use at once.
        # Only the committee calls themselves run concurrently.
        #
        # They used to run one after the other inside a split budget, so a plan
        # cost the sum of two fan-outs. Run together, it costs the slower one,
        # and neither stage can starve the other of time: each gets the whole
        # allowance on its own deadline.
        held_stage = await self._judge_held(held, passed, use_ai, macro, researched, brief)
        cand_stage = await self._judge_candidates(
            user_id, body, passed, use_ai, macro, researched, brief
        )
        (held_out, held_fresh), (cand_out, cand_fresh) = await asyncio.gather(
            self._run_stage(held_stage, use_ai), self._run_stage(cand_stage, use_ai)
        )
        held_results, held_prices, held_names = held_out
        candidates, considered = cand_out
        await self._remember(held_fresh, held=True)
        await self._remember(cand_fresh, held=False)
        # Each verdict degrades on its own, so "the AI ran" is not a property of
        # the request — it is a count of which ones actually came back. Saying
        # "ai" over a plan that is mostly rules would overstate it.
        verdicts = [*held_results.values(), *candidates]
        source = self._source(verdicts)
        ai_note = ai_offline_note(
            source,
            verdicts,
            requested=body.use_ai,
            configured=settings.ai_available,
            pool_status=getattr(self.llm, "status", None),
        )

        # Refit over holdings and shortlist together. The overlay compares the
        # book as it stands to the book the plan would leave, and that
        # comparison is only meaningful when both sides were measured over one
        # window — otherwise the change in window shows up as an effect of the
        # trades. Costs one extra alignment over a handful of cached series.
        if candidates:
            model = (
                await self._fit_factors(
                    {**currencies, **{c["symbol"]: c.get("currency") for c in candidates}}
                )
                or model
            )

        sized = self._size(
            body=body,
            equity=equity,
            cash=cash,
            held=held,
            held_results=held_results,
            held_prices=held_prices,
            held_names=held_names,
            candidates=candidates,
            passed=passed,
            model=model,
            limits=limits,
        )
        actions, holds = _with_signals(
            sized.actions, sized.holds, [*held_results.values(), *candidates]
        )
        actions = _with_prices(actions, self._quote_meta)
        cash_after, unfunded = sized.cash_after, sized.unfunded

        return ActionPlanOut(
            as_of=date.today().isoformat(),
            equity=round(equity, 2),
            cash_before=round(cash, 2),
            cash_after=round(cash_after, 2),
            sizing=body.sizing,
            actions=actions,
            holds=holds,
            passed=self.deploy._trim_passed(passed),
            considered=considered + len(held),
            source=source,
            ai_note=ai_note,
            note=self._note(actions, holds, cash_after, body, unfunded, sized.factors),
            factors=sized.factors,
            period=self._period.isoformat(),
            research=research_out(
                MarketContext(as_of=date.today(), macro=macro, symbols=researched)
                if (macro is not None or researched)
                else None
            ),
        )

    # --- factor model ---
    async def _fit_factors(
        self, currency_by_symbol: dict[str, str | None]
    ) -> factor_risk.FactorModel | None:
        """Regress everything this plan might touch on the factor proxies.

        Returns None whenever the fit cannot be trusted — too little history,
        a missing proxy panel, a provider that failed. Nothing downstream
        treats that as an error: the plan simply runs as it did before the
        factor work existed, which is the right failure for a guardrail. The
        alternative, enforcing limits computed from three months of one ETF,
        would decline trades for a reason nobody could check.
        """
        symbols = [s for s in dict.fromkeys(currency_by_symbol) if s]
        if not symbols:
            return None
        start = date.today() - timedelta(days=FACTOR_WINDOW_DAYS)
        try:
            _, returns, _ = await self.invest._aligned_returns(
                symbols + list(factor_risk.PROXY_SYMBOLS), start
            )
            f_returns, unavailable = factor_risk.build_factor_returns(returns)
            # Regress on what a CAD holder actually earns. Without this a
            # US-listed name shows no USD/CAD loading at all — its own price
            # series never contains the currency move — and the FX ceiling
            # would be guarding a number that cannot rise.
            asset_returns = factor_risk.to_reporting_currency(
                {s: returns[s] for s in symbols if s in returns},
                currency_by_symbol,
                returns.get(FX_SYMBOL),
            )
            return factor_risk.fit_model(symbols, asset_returns, f_returns, unavailable)
        except Exception as exc:  # noqa: BLE001 — a plan without factors is still a plan
            logger.warning("Factor fit failed, planning without limits: %s", exc)
            return None

    # --- stage 1: what you already own ---
    async def _judge_held(
        self,
        held: dict[str, dict],
        passed: list[DeployPassedOut],
        use_ai: bool,
        macro: MacroBrief | None = None,
        researched: dict[str, SymbolNews] | None = None,
        brief: PortfolioBrief | None = None,
    ) -> _Stage:
        """Committee verdict on the held positions, largest first.

        Held positions are never shortlisted *away* — a plan that silently
        ignores something you own is worse than no plan — but they are the only
        uncapped fan-out in the app, and one metered call each meant a
        twenty-name portfolio spent twenty requests on a single tap, most of
        them on positions too small to act on.

        So the biggest positions get the model and the tail gets the same
        pre-committed rules the auto-trader runs on, reported honestly rather
        than dropped. Ordering by weight is what makes the cut defensible: the
        money is judged, the rounding is not.
        """
        symbols = sorted(held, key=lambda s: held[s].get("weight", 0.0), reverse=True)
        if not symbols:
            return _Stage.done(({}, {}, {}))

        if len(symbols) > settings.max_held_judged:
            for symbol in symbols[settings.max_held_judged :]:
                passed.append(
                    DeployPassedOut(
                        symbol=symbol,
                        action="HOLD",
                        reason=(
                            "too small to spend an AI call on this run — judged on the "
                            "pre-committed rules instead."
                        ),
                    )
                )
            symbols = symbols[: settings.max_held_judged]

        # Bars were synced by `plan` before the portfolio was valued. Only
        # completed sessions feed the signal: today's bar moves all day.
        series = await self.market_data.get_close_series(symbols)
        scored: list[tuple[float, str, Indicators, str]] = []
        for symbol in symbols:
            closes = completed_closes(series.get(symbol, []), self._period)
            try:
                ind = compute_indicators(closes)
            except ValueError:
                passed.append(
                    DeployPassedOut(
                        symbol=symbol,
                        action="unpriced",
                        reason="not enough price history to judge — left untouched.",
                    )
                )
                continue
            scored.append((0.0, symbol, ind, "hold"))

        quoted, unpriced = await self.deploy._quote(scored)
        self._remember_quotes(quoted)
        passed.extend(unpriced)
        if not quoted:
            return _Stage.done(({}, {}, {}))

        # The seats are told this is already owned — and judge TRIM/HOLD/ADD
        # rather than BUY/SKIP/HOLD, because "I would not open this today" is
        # true of most things most of the time and is not a reason to sell.
        contexts = {
            sym: HeldContext(
                weight=held[sym]["weight"],
                unrealized_pct=held[sym]["unrealized_pl_pct"],
            )
            for sym, _, _, _ in quoted
            if sym in held
        }
        # Held positions ALWAYS get news. There are only ever a handful, and a
        # fresh negative event on something you already own is the single
        # highest-value case in this whole feature — it is the one the price
        # series cannot warn you about in time.
        market = None
        if use_ai and self.context is not None:
            by_symbol, stale = await self.context.for_symbols([s for s, _, _, _ in quoted])
            await self.session.commit()
            if researched is not None:
                researched.update(by_symbol)
            market = MarketContext(
                as_of=date.today(),
                macro=macro,
                symbols=by_symbol,
                stale=bool(stale),
                stale_symbols=tuple(stale),
            )

        inds = {sym: ind for sym, _, ind, _ in quoted}
        return _Stage(
            items=[(sym, quote, ind) for sym, quote, ind, _ in quoted],
            options={
                "held": contexts,
                "rules": {
                    sym: rules_verdict(ind, held=True, symbol=sym) for sym, _, ind, _ in quoted
                },
                "market": market,
                "portfolio": brief,
                "second_opinion": settings.llm_second_opinion_on_sells,
            },
            finish=lambda results: (
                {
                    r["symbol"]: anchor_to_signal(r, inds.get(r["symbol"]), held=True)
                    for r in results
                },
                {sym: price for sym, _, _, price in quoted},
                {sym: quote.get("name") for sym, quote, _, _ in quoted},
            ),
            cached=await self._cached(list(inds), held=True, use_ai=use_ai),
            held=True,
        )

    # --- stage 2: what you could own instead ---
    async def _judge_candidates(
        self,
        user_id: uuid.UUID,
        body: ActionPlanIn,
        passed: list[DeployPassedOut],
        use_ai: bool,
        macro: MacroBrief | None = None,
        researched: dict[str, SymbolNews] | None = None,
        brief: PortfolioBrief | None = None,
    ) -> _Stage:
        """Rank the unheld universe for free, then spend calls on the top few."""
        if body.max_new_positions == 0:
            return _Stage.done(([], 0))

        pool = await self.deploy._candidate_pool(user_id)
        if not pool:
            return _Stage.done(([], 0))

        await self.market_data.sync_bars(pool)
        await self.session.commit()

        ranked, unpriced = await self.deploy._rank(pool, before=self._period)
        passed.extend(unpriced)
        if not ranked:
            return _Stage.done(([], len(pool)))

        quoted, no_quote = await self.deploy._quote(ranked[: body.max_candidates])
        self._remember_quotes(quoted)
        passed.extend(no_quote)
        if not quoted:
            return _Stage.done(([], len(pool)))

        # Shortlist only — `pool` above may be ~65 names, `quoted` is at most
        # max_candidates. Fetching news for the pool is what this staging exists
        # to avoid.
        market = None
        if use_ai and self.context is not None:
            by_symbol, stale = await self.context.for_symbols([s for s, _, _, _ in quoted])
            await self.session.commit()
            if researched is not None:
                researched.update(by_symbol)
            market = MarketContext(
                as_of=date.today(),
                macro=macro,
                symbols=by_symbol,
                stale=bool(stale),
                stale_symbols=tuple(stale),
            )

        prices = {sym: price for sym, _, _, price in quoted}
        names = {sym: quote.get("name") for sym, quote, _, _ in quoted}
        inds = {sym: ind for sym, _, ind, _ in quoted}
        return _Stage(
            items=[(sym, quote, ind) for sym, quote, ind, _ in quoted],
            options={
                "rules": {
                    sym: rules_verdict(ind, held=False, symbol=sym) for sym, _, ind, _ in quoted
                },
                "market": market,
                "portfolio": brief,
            },
            finish=lambda results: (
                [
                    {
                        **anchor_to_signal(r, inds.get(r["symbol"]), held=False),
                        "price_cad": prices[r["symbol"]],
                        "name": names.get(r["symbol"]),
                    }
                    for r in results
                ],
                len(pool),
            ),
            cached=await self._cached(list(inds), held=False, use_ai=use_ai),
        )

    async def _run_stage(self, stage: _Stage, use_ai: bool) -> tuple[tuple, list[dict]]:
        """The committee call a stage prepared, on its own deadline.

        Returns what the stage produces plus the verdicts that were freshly
        asked for (to be remembered for the rest of the day). Symbols with a
        verdict already on file for today are not asked again.
        """
        if not stage.items:
            return stage.finish([]), []
        todo = [item for item in stage.items if item[0] not in stage.cached]
        fresh: list[dict] = []
        if todo:
            with llm_budget(
                wait=settings.llm_wait_actions_seconds,
                max_pass_sleep=settings.llm_max_pass_sleep_seconds,
                max_output_tokens=settings.llm_committee_max_tokens,
            ):
                fresh = await run_committee_many(todo, self.llm, use_ai=use_ai, **stage.options)
        by_symbol = {**stage.cached, **{r["symbol"]: r for r in fresh}}
        results = [by_symbol[sym] for sym, _, _ in stage.items if sym in by_symbol]
        return stage.finish(results), fresh

    def _remember_quotes(self, quoted: list[tuple[str, dict, Indicators, float]]) -> None:
        """Where each price came from and when, for the plan's lines."""
        for sym, quote, _, _ in quoted:
            self._quote_meta[sym] = (quote.get("source"), quote.get("as_of"))

    # --- the day's verdicts ---
    async def _cached(self, symbols: list[str], *, held: bool, use_ai: bool) -> dict[str, dict]:
        """Committee verdicts already reached today for these symbols."""
        if not use_ai or not symbols:
            return {}
        try:
            return await VerdictCacheRepository(self.session).get_many(
                self._user_id, symbols, held=held, period=self._period
            )
        except Exception as exc:  # noqa: BLE001 — a cache miss is never fatal
            logger.warning("Verdict cache read failed: %s", exc)
            return {}

    async def _remember(self, fresh: list[dict], *, held: bool) -> None:
        """Pin today's AI verdicts. Rules fallbacks are not stored: the next
        run should try the model again rather than inherit an outage."""
        ai = [r for r in fresh if r.get("source") == "ai"]
        if not ai:
            return
        try:
            await VerdictCacheRepository(self.session).put_many(
                self._user_id, ai, held=held, period=self._period
            )
            await self.session.commit()
        except Exception as exc:  # noqa: BLE001 — failing to cache is not failing to plan
            logger.warning("Verdict cache write failed: %s", exc)
            await self.session.rollback()

    # --- stage 3: verdicts -> orders against one budget ---
    def _size(
        self,
        *,
        body: ActionPlanIn,
        equity: float,
        cash: float,
        held: dict[str, dict],
        held_results: dict[str, dict],
        held_prices: dict[str, float],
        held_names: dict[str, str | None],
        candidates: list[dict],
        passed: list[DeployPassedOut],
        model: factor_risk.FactorModel | None = None,
        limits: factor_limits.Limits | None = None,
    ) -> _Sized:
        sells: list[ActionOut] = []
        holds: list[ActionHoldOut] = []
        adds: list[dict] = []
        keep: list[tuple[str, dict, dict, float]] = []  # symbol, position, result, price
        # Value freed by the sells, tracked per symbol so a name being trimmed
        # is never topped up in the same plan.
        trimmed: dict[str, float] = {}
        # The subset of that raised by selling on a verdict the committee
        # actually reached. Only these proceeds are restricted below — a
        # concentration trim is the app's own rule firing, not a view the model
        # took, and its stated reason is to redeploy.
        ai_trimmed: dict[str, float] = {}

        # --- what the committee said about each holding ---
        for symbol, position in held.items():
            result = held_results.get(symbol)
            price = held_prices.get(symbol)
            if result is None or not price:
                # No quote or not enough history. Say so — a holding that
                # silently disappears from the plan reads as a recommendation
                # to do nothing, which is not what happened.
                holds.append(
                    ActionHoldOut(
                        symbol=symbol,
                        weight=position["weight"],
                        confidence=0.0,
                        reason="couldn't be priced or judged today — left untouched.",
                    )
                )
                continue

            action = str(result.get("action", "HOLD")).upper()
            confidence = round(float(result.get("confidence") or 0), 2)
            summary = str(result.get("summary") or "").strip()[:400]
            value = position["market_value"]

            if action == "TRIM":
                fraction = trim_fraction(result, confidence)
                sell_value = min(value * fraction, value)
                if sell_value >= MIN_TRADE_CAD:
                    sell = self._sell_action(
                        symbol=symbol,
                        position=position,
                        name=position.get("name") or held_names.get(symbol),
                        price=price,
                        sell_value=sell_value,
                        label="EXIT" if fraction >= 1.0 else "TRIM",
                        equity=equity,
                        confidence=confidence,
                        reason=summary or "the committee's case for reducing it.",
                        sizing=body.sizing,
                    )
                    if sell is not None:
                        trimmed[symbol] = sell.amount
                        if str(result.get("source")) == "ai":
                            ai_trimmed[symbol] = sell.amount
                        sells.append(sell)
                        continue
                    holds.append(
                        ActionHoldOut(
                            symbol=symbol,
                            weight=position["weight"],
                            confidence=confidence,
                            reason=(
                                "worth trimming, but the trim is smaller than one "
                                "share. Switch to dollar amounts to act on it."
                            ),
                        )
                    )
                    continue

            if action == "ADD":
                adds.append(
                    {
                        "symbol": symbol,
                        "name": position.get("name") or held_names.get(symbol),
                        "price_cad": price,
                        "confidence": confidence,
                        "summary": summary,
                        "current_value": value,
                        "label": "ADD",
                        "verdict_source": str(result.get("source") or "rules"),
                        **_signal_terms(result),
                    }
                )
                continue

            keep.append((symbol, position, result, price))

        # --- new names worth opening ---
        buys = [c for c in candidates if str(c.get("action", "")).upper() == "BUY"]
        buys.sort(key=lambda c: float(c.get("confidence") or 0), reverse=True)
        for c in buys[: body.max_new_positions]:
            adds.append(
                {
                    "symbol": c["symbol"],
                    "name": c.get("name"),
                    "price_cad": c["price_cad"],
                    "confidence": round(float(c.get("confidence") or 0), 2),
                    "summary": str(c.get("summary") or "").strip()[:400],
                    "current_value": 0.0,
                    "label": "NEW",
                    "verdict_source": str(c.get("source") or "rules"),
                    **_signal_terms(c),
                }
            )

        # Never fund a buy on a weaker signal than the sell paying for it. When
        # the model answers for some tickers and not others — free-tier capacity
        # comes and goes mid-plan — the naive result is selling what the
        # committee scrutinized to buy what only the rules saw. A switch
        # justified by the weaker of two signals is not a switch worth making.
        #
        # The rule is about the *swap*, though, and this used to enforce it by
        # deleting every rules-sourced buy outright. That is a much wider claim:
        # it says money you already had may not be invested either, because
        # something unrelated was sold on a better signal. On a metered endpoint
        # the candidate stage is the one that degrades, so the standing result
        # was a plan that only ever sold.
        #
        # So the restriction now lands where the principle actually applies —
        # the proceeds, not the buy. A rules-sourced buy may draw on cash on
        # hand and on what a rules-driven sell raised; only the proceeds of a
        # sell the committee itself judged are reserved for buys it also judged.
        # With no sells at all, nothing is restricted and nothing changes.

        # --- concentration overlay ---
        # A name can be a fine investment and still be too much of the account,
        # so this runs regardless of the verdict — but only when the proceeds
        # have somewhere else to go. Selling a position down to sit in cash is
        # not diversification, and a single broad ETF at 100% is a deliberate
        # strategy rather than a mistake to correct.
        #
        # The ceiling is risk-based: a fixed share of the account's volatility
        # per name (see quant_signals.vol_capped_weight), never above the flat
        # cap. It only fires once the position has drifted a band past it, so
        # ordinary drift doesn't turn into a trade every time you ask.
        for symbol, position, result, price in keep:
            weight = position["weight"]
            vol = _signal_terms(result).get("vol")
            ceiling = vol_capped_weight(vol, CONCENTRATION_CAP, symbol)
            over = weight > CONCENTRATION_CAP or weight > ceiling * REBALANCE_BAND
            excess = max(0.0, (weight - ceiling) * equity) if over else 0.0
            elsewhere = [a for a in adds if a["symbol"] != symbol]
            if excess < MIN_TRADE_CAD or not elsewhere:
                holds.append(
                    ActionHoldOut(
                        symbol=symbol,
                        weight=weight,
                        confidence=round(float(result.get("confidence") or 0), 2),
                        reason=str(result.get("summary") or "").strip()[:400]
                        or "no change warranted.",
                    )
                )
                continue

            sell = self._sell_action(
                symbol=symbol,
                position=position,
                name=position.get("name") or held_names.get(symbol),
                price=price,
                sell_value=excess,
                label="TRIM",
                equity=equity,
                confidence=round(float(result.get("confidence") or 0), 2),
                reason=(
                    f"{symbol} is {weight:.0%} of the account, above the "
                    f"{CONCENTRATION_CAP:.0%} single-position limit — trimming back "
                    "to it and redeploying."
                    if ceiling >= CONCENTRATION_CAP
                    else f"{symbol} is {weight:.0%} of the account; at its volatility "
                    f"({(vol or 0):.0%} a year) one name should be about {ceiling:.0%} so "
                    "no single stock dominates the risk — trimming back to that and "
                    "redeploying."
                ),
                sizing=body.sizing,
            )
            if sell is None:
                holds.append(
                    ActionHoldOut(
                        symbol=symbol,
                        weight=weight,
                        confidence=round(float(result.get("confidence") or 0), 2),
                        reason=(
                            f"over the {CONCENTRATION_CAP:.0%} limit, but the trim is "
                            "smaller than one share. Switch to dollar amounts to act on it."
                        ),
                    )
                )
                continue
            trimmed[symbol] = sell.amount
            sells.append(sell)

        budget = cash + sum(trimmed.values())
        # Keep roughly 2% of the account in cash — but never at the cost of most
        # of the budget. Someone holding less than the buffer is asking what to
        # buy, not to be told they may not invest until they have more.
        reserve = min(equity * CASH_FLOOR_PCT, budget * MAX_RESERVE_SHARE)
        spendable = max(0.0, budget - reserve)

        # Everything except the proceeds of a committee-judged sell. This is the
        # ceiling on what buys the committee did not judge may draw between
        # them; each one is still sized by conviction inside it.
        weak_ceiling = max(0.0, spendable - sum(ai_trimmed.values()))

        purchases, unaffordable, unfunded = self._allocate_buys(
            adds, spendable, equity, body.sizing, weak_ceiling=weak_ceiling
        )

        # --- factor overlay ---
        # Last, and only over the buys. Everything above decided WHAT to own;
        # this decides how much of the resulting book may sit on any one kind
        # of risk. It runs after allocation rather than as another cap inside
        # it because the limits are properties of the whole plan — you cannot
        # tell whether a name breaches the equity share of risk by looking at
        # that name.
        purchases, cut, factor_plan = self._apply_factor_limits(
            purchases,
            held=held,
            trimmed=trimmed,
            equity=equity,
            model=model,
            limits=limits,
            sizing=body.sizing,
        )
        for a in cut:
            if a["symbol"] in held:
                holds.append(
                    ActionHoldOut(
                        symbol=a["symbol"],
                        weight=held[a["symbol"]]["weight"],
                        confidence=a["confidence"],
                        reason=a["reason"],
                    )
                )
            else:
                passed.append(
                    DeployPassedOut(
                        symbol=a["symbol"],
                        action="factor-limit",
                        confidence=a["confidence"],
                        reason=a["reason"],
                    )
                )
        cash_after = budget - sum(p.amount for p in purchases)

        # A name that cannot be bought in whole shares is still a decision the
        # user should see — it is why their money stayed in cash.
        for a in unaffordable:
            if a["symbol"] in held:
                holds.append(
                    ActionHoldOut(
                        symbol=a["symbol"],
                        weight=round(a["current_value"] / equity, 4) if equity else 0.0,
                        confidence=a["confidence"],
                        reason=a["reason"],
                    )
                )
            else:
                passed.append(
                    DeployPassedOut(
                        symbol=a["symbol"],
                        action="too-expensive",
                        confidence=a["confidence"],
                        reason=a["reason"],
                    )
                )

        # Anything held that produced no order at all — usually a name worth
        # adding to that the budget could not stretch to — is reported as a
        # deliberate hold. Every position the user owns must be accounted for
        # somewhere in the plan; silence is not an answer about their money.
        acted = {a.symbol for a in sells} | {p.symbol for p in purchases}
        spoken_for = acted | {h.symbol for h in holds}
        for a in adds:
            if a["symbol"] in held and a["symbol"] not in spoken_for:
                holds.append(
                    ActionHoldOut(
                        symbol=a["symbol"],
                        weight=round(a["current_value"] / equity, 4) if equity else 0.0,
                        confidence=a["confidence"],
                        reason=(
                            "worth adding to, but the budget went to higher-conviction ideas first."
                            if purchases
                            else "worth adding to, but there wasn't enough spare cash "
                            f"to make a trade worth placing (minimum ${MIN_TRADE_CAD:,.0f})."
                        ),
                    )
                )
                spoken_for.add(a["symbol"])
        for symbol, position in held.items():
            if symbol not in spoken_for:
                holds.append(
                    ActionHoldOut(
                        symbol=symbol,
                        weight=position["weight"],
                        confidence=0.0,
                        reason="no change warranted.",
                    )
                )

        # Sells first: the buys are funded by them.
        return _Sized(
            actions=sells + purchases,
            holds=holds,
            cash_after=cash_after,
            unfunded=unfunded,
            factors=factor_plan,
        )

    def _apply_factor_limits(
        self,
        purchases: list[ActionOut],
        *,
        held: dict[str, dict],
        trimmed: dict[str, float],
        equity: float,
        model: factor_risk.FactorModel | None,
        limits: factor_limits.Limits | None,
        sizing: Sizing,
    ) -> tuple[list[ActionOut], list[dict], FactorPlanOut | None]:
        """Cut the buys back to what the factor limits allow.

        Returns the surviving orders, the ones cut to nothing (to be reported
        rather than silently dropped — money left in cash is a decision), and
        the factor block for the response.
        """
        if model is None:
            return (
                purchases,
                [],
                FactorPlanOut(
                    reason=(
                        "Not enough overlapping price history across your holdings and the "
                        "factor proxies to measure what kind of risk you carry — tap Sync data."
                    )
                ),
            )
        limits = limits or _limits()

        # The book the plan would leave behind, before any buying: sells have
        # already been decided and their proceeds are what funds the buys.
        positions = {
            symbol: max(0.0, position["market_value"] - trimmed.get(symbol, 0.0))
            for symbol, position in held.items()
        }
        by_symbol = {p.symbol: p for p in purchases}
        result = factor_limits.constrain(
            model,
            equity=equity,
            positions=positions,
            buys={p.symbol: p.amount for p in purchases},
            limits=limits,
        )

        # An enforcement pass that never checks whether the check ran would be
        # the worst of both: silent when it mattered. `state_now` is None when
        # the model priced too little of the book to speak for it.
        if result.state_now is None:
            return (
                purchases,
                [],
                FactorPlanOut(
                    reason=(
                        "Too little of your portfolio has usable price history to break its "
                        "risk into factors, so no factor limit was applied to this plan."
                    )
                ),
            )

        kept: list[ActionOut] = []
        cut: list[dict] = []
        binding = next((b.name for b in result.breaches if b.binding), None)
        enforcing = settings.factor_limits_enabled
        for symbol, amount in result.buys.items():
            order = by_symbol[symbol]
            # Report-only mode still measures and still explains; it just does
            # not move anyone's money. Worth keeping as one flag rather than
            # two code paths, so what ships is what was tested.
            if not enforcing or amount >= order.amount - 0.005:
                kept.append(order)
                continue
            resized = self._resize_buy(order, amount, equity=equity, sizing=sizing)
            note = f" Sized down to stay inside the {binding} limit." if binding else ""
            if resized is None:
                cut.append(
                    {
                        "symbol": symbol,
                        "confidence": order.confidence,
                        "reason": (
                            f"worth owning, but buying it would push this portfolio past the "
                            f"{binding} limit, and what was left is too small to trade."
                            if binding
                            else "cut back by the factor limits to too small a trade to place."
                        ),
                    }
                )
                continue
            kept.append(resized.model_copy(update={"reason": resized.reason + note}))

        kept.sort(key=lambda p: p.amount, reverse=True)
        return kept, cut, self._factor_plan(result, enforced=enforcing and result.enforced)

    @staticmethod
    def _resize_buy(
        order: ActionOut, amount: float, *, equity: float, sizing: Sizing
    ) -> ActionOut | None:
        """The same buy, smaller. None when what is left is not worth placing."""
        if sizing == "shares":
            whole = float(int(amount / order.price)) if order.price else 0.0
            if whole < 1:
                return None
            amount, qty = whole * order.price, whole
        else:
            qty = None
        if amount < MIN_TRADE_CAD:
            return None
        amount = round(amount, 2)
        current_value = order.current_weight * equity
        return order.model_copy(
            update={
                "amount": amount,
                "qty": qty,
                "projected_weight": (
                    round((current_value + amount) / equity, 4) if equity else 0.0
                ),
            }
        )

    @staticmethod
    def _factor_plan(result: factor_limits.Constrained, *, enforced: bool) -> FactorPlanOut:
        now, after = result.state_now, result.state_after
        if now is None:
            return FactorPlanOut(enforced=False)
        after = after or now
        return FactorPlanOut(
            factors=[
                PlanFactorOut(
                    key=key,
                    name=(
                        factor_risk.FACTOR_BY_KEY[key].name
                        if key in factor_risk.FACTOR_BY_KEY
                        else key
                    ),
                    exposure=round(exposure, 4),
                    projected_exposure=round(after.exposures.get(key, exposure), 4),
                    share_of_risk=round(now.variance_share.get(key, 0.0), 4),
                    projected_share_of_risk=round(after.variance_share.get(key, 0.0), 4),
                    duration_years=(
                        round(now.duration_years, 2)
                        if key == "ir" and now.duration_years is not None
                        else None
                    ),
                    projected_duration_years=(
                        round(after.duration_years, 2)
                        if key == "ir" and after.duration_years is not None
                        else None
                    ),
                )
                for key, exposure in now.exposures.items()
            ],
            idiosyncratic_share=round(now.idiosyncratic_share, 4),
            projected_idiosyncratic_share=round(after.idiosyncratic_share, 4),
            total_vol=round(now.total_vol, 4),
            projected_total_vol=round(after.total_vol, 4),
            breaches=[
                FactorBreachOut(
                    key=b.key,
                    name=b.name,
                    current=b.current,
                    projected=b.projected,
                    limit=b.limit,
                    # A breach the app chose not to act on must not claim it
                    # bound anything, or the plan would describe a cut it did
                    # not make.
                    binding=b.binding and enforced,
                    unit=b.unit,
                    message=b.message,
                )
                for b in result.breaches
            ],
            enforced=enforced,
        )

    @staticmethod
    def _sell_action(
        *,
        symbol: str,
        position: dict,
        name: str | None,
        price: float,
        sell_value: float,
        label: str,
        equity: float,
        confidence: float,
        reason: str,
        sizing: str,
    ) -> ActionOut | None:
        value = position["market_value"]
        # Price the sell off the position itself, not the live quote. The two
        # differ by however stale the cached close is, and sizing against one
        # while quoting the other is how a "sell half" turns into an order for
        # more shares than are held.
        unit_price = value / position["qty"] if position["qty"] else price
        qty = None
        if sizing != "dollars":
            held_qty = position["qty"]
            wanted = sell_value / unit_price if unit_price else 0.0
            qty = min(float(int(wanted)), float(int(held_qty)))
            # A fractional holding rounds to zero shares, which would propose a
            # $0 order. Selling one out entirely is a real order at any broker
            # that sold you the fraction, so a full exit keeps its quantity.
            if qty < 1 and held_qty < 1 and wanted >= held_qty - 1e-9:
                qty = held_qty
            if qty <= 0:
                # A partial trim smaller than one share of a multi-share holding
                # cannot be expressed in whole shares. Returning a zero-quantity
                # order would put "0 shares" on the checklist next to a real
                # dollar figure, which is worse than saying it can't be done.
                return None
            sell_value = qty * unit_price
        return ActionOut(
            symbol=symbol,
            name=name,
            side="sell",
            action=label,
            amount=round(sell_value, 2),
            qty=qty,
            price=round(unit_price, 4),
            current_weight=position["weight"],
            projected_weight=round(max(0.0, (value - sell_value) / equity), 4) if equity else 0.0,
            confidence=confidence,
            reason=reason,
        )

    def _allocate_buys(
        self,
        adds: list[dict],
        spendable: float,
        equity: float,
        sizing: Sizing = "dollars",
        *,
        weak_ceiling: float | None = None,
    ) -> tuple[list[ActionOut], list[dict], bool]:
        """Split the budget across the buys by conviction, capped per name.

        The cap is on the *resulting* weight, not the purchase: topping up
        something already large is exactly how a portfolio drifts into a single
        bet, and the committee liking a name is not a reason to allow it.

        Under share sizing the allocation is rounded down to whole shares, so a
        name whose share price exceeds its slice cannot be funded at all — an
        allocation of $94 against a $1,053 share is not a small buy, it is no
        buy. Those come back in the second list to be reported rather than
        proposed as an order nobody can place.

        ``weak_ceiling`` bounds what buys the committee did not judge may draw
        between them — the money that did not come from selling on a verdict.
        The third return value says whether it actually bit, so the note can
        explain the cash rather than leave it unaccounted for.
        """
        if not adds or spendable < MIN_TRADE_CAD:
            return [], [], False

        # Highest conviction first; ties go to the smaller position, since
        # adding to what you already hold most of is the one outcome nobody is
        # asking for.
        adds = sorted(adds, key=lambda a: (-a["confidence"], a["current_value"]))
        floor = max(MIN_TRADE_CAD, equity * MEANINGFUL_TRADE_PCT)
        adds = adds[: max(1, int(spendable // floor))]

        # Sized by expected edge per unit of risk (score / volatility, see
        # quant_signals.alpha_weight) when the signal is known, by conviction
        # otherwise.
        weights = {
            a["symbol"]: (
                alpha_weight(a["score"], a.get("vol"))
                if a.get("score") is not None
                else max(a["confidence"], 0.01)
            )
            for a in adds
        }
        total = sum(weights.values())
        purchases: list[ActionOut] = []
        unaffordable: list[dict] = []
        headroom = spendable if weak_ceiling is None else weak_ceiling
        unfunded = False
        for a in adds:
            symbol = a["symbol"]
            allocation = spendable * (weights[symbol] / total)
            # Room left before this name breaches its cap: the flat
            # concentration limit, tightened for a volatile single name.
            cap = vol_capped_weight(a.get("vol"), CONCENTRATION_CAP, symbol)
            room = max(0.0, cap * equity - a["current_value"])
            # Rounded to cents HERE rather than just before the order is built,
            # so the figure compared against `headroom` is the same one that
            # later decrements it. Rounding afterwards let a fraction of a cent
            # per buy accumulate in `headroom`, and by the fifth name the gap
            # was wide enough to read as a shortfall: a plan that funded every
            # idea it had still told the user the model was only partly
            # reachable and that a sell's proceeds were being held back — from
            # sells it had not made.
            allocation = round(min(allocation, room), 2)
            # A buy the committee did not judge may only be paid for out of the
            # money no committee-judged sell raised. Highest conviction is first
            # in this loop, so the headroom goes to the strongest ideas.
            weak = a.get("verdict_source") != "ai"
            if weak:
                capped = min(allocation, headroom)
                unfunded = unfunded or capped < allocation - FUNDING_SHORTFALL_CAD
                allocation = capped
            price = a["price_cad"]

            qty = None
            if sizing == "shares":
                whole = float(int(allocation / price)) if price else 0.0
                if whole < 1:
                    unaffordable.append(
                        {
                            **a,
                            "reason": (
                                f"one share is ${price:,.2f} — more than the "
                                f"${allocation:,.2f} this name's slice came to. "
                                "Switch to dollar amounts to buy a fraction."
                            ),
                        }
                    )
                    continue
                qty = whole
                allocation = whole * price

            if allocation < MIN_TRADE_CAD:
                continue
            allocation = round(allocation, 2)
            if weak:
                headroom -= allocation
            projected = (a["current_value"] + allocation) / equity if equity else 0.0
            purchases.append(
                ActionOut(
                    symbol=symbol,
                    name=a.get("name"),
                    side="buy",
                    action=a["label"],
                    amount=allocation,
                    qty=qty,
                    price=price,
                    current_weight=round(a["current_value"] / equity, 4) if equity else 0.0,
                    projected_weight=round(projected, 4),
                    confidence=a["confidence"],
                    reason=a["summary"] or "the committee would open this today.",
                )
            )
        purchases.sort(key=lambda p: p.amount, reverse=True)
        return purchases, unaffordable, unfunded

    _source = staticmethod(source_of)

    @staticmethod
    def _note(
        actions: list[ActionOut],
        holds: list[ActionHoldOut],
        cash_after: float,
        body: ActionPlanIn,
        unfunded: bool = False,
        factors: FactorPlanOut | None = None,
    ) -> str:
        # Named separately from the committee's reasoning, because it is not
        # the committee's reasoning: it is the app declining to concentrate the
        # book further whatever the model concluded.
        capped = ""
        if factors is not None:
            binding = [b for b in factors.breaches if b.binding]
            if binding:
                capped = " " + binding[0].message[0].upper() + binding[0].message[1:]
            elif factors.breaches:
                capped = " " + factors.breaches[0].message

        if not actions:
            kept = ", ".join(h.symbol for h in holds)
            return (
                "No changes worth making. The committee would keep "
                f"{kept or 'what you hold'} as it is, and nothing on the shortlist "
                "beat what you already own. Doing nothing is a position." + capped
            )
        sells = [a for a in actions if a.side == "sell"]
        buys = [a for a in actions if a.side == "buy"]
        parts = []
        if sells:
            parts.append("sell " + ", ".join(f"${a.amount:,.0f} of {a.symbol}" for a in sells))
        if buys:
            parts.append("put " + ", ".join(f"${a.amount:,.0f} into {a.symbol}" for a in buys))
        parked = (
            " The model was only partly reachable. What a sell it judged raised is "
            "held back rather than put into an idea it never got to judge, so part "
            "of the proceeds stays in cash."
            if unfunded
            else ""
        )
        sizing_note = (
            " Dollar amounts, so this needs a broker that sells fractional shares."
            if body.sizing == "dollars"
            else " Whole shares only."
        )
        return (
            "Proposed: "
            + "; then ".join(parts)
            + f". ${cash_after:,.2f} stays in cash."
            + parked
            + capped
            + sizing_note
            + " Accepting adds these to your trade checklist — nothing is placed "
            "until you approve each one."
        )

    # --- accepting (writes PROPOSED rows — never APPROVED) ---
    async def accept(self, user_id: uuid.UUID, body: ActionAcceptIn) -> ProposalOut:
        batch_id = uuid.uuid4()
        dollars = body.sizing == "dollars"

        # No over-sell check here on purpose. A dollar-sized sell for more than
        # the position is *defined* as a full exit (see `_filled_qty`), so a
        # rule at this boundary would reject the very thing the checklist means
        # by "close it out". Both consumers of a PROPOSED row guard themselves
        # instead: `apply_fill` refuses to book a sell the position cannot
        # cover, and `approve_and_submit` refuses to route one to a broker.
        orders = [
            ProposedOrder(
                user_id=user_id,
                batch_id=batch_id,
                kind="actions",
                symbol=o.symbol.strip().upper(),
                side=OrderSide.BUY if o.side == "buy" else OrderSide.SELL,
                order_type="market",
                qty=None if dollars else (o.qty or round(o.amount / o.price, 4)),
                notional_cents=round(o.amount * 100) if dollars else None,
                est_value_cents=round(o.amount * 100),
                reason=(
                    o.reason.strip()
                    or f"{'Sell' if o.side == 'sell' else 'Buy'} ${o.amount:,.2f} CAD "
                    f"of {o.symbol.upper()} from your AI portfolio plan."
                ),
            )
            for o in body.orders
        ]
        # Never supersede an open checklist without a batch to replace it with:
        # expiring on an empty accept would clear orders the user is still
        # working through. Same invariant as the rebalancer.
        if orders:
            await self.proposals.expire_open_proposals(user_id)
        await self.proposals.add_all(orders)
        await self.session.commit()

        return ProposalOut(
            batch_id=str(batch_id),
            orders=[order_out(o) for o in orders],
            total_buy=round(
                sum(cents_to_dollars(o.est_value_cents) for o in orders if o.side == OrderSide.BUY),
                2,
            ),
            total_sell=round(
                sum(
                    cents_to_dollars(o.est_value_cents) for o in orders if o.side == OrderSide.SELL
                ),
                2,
            ),
            note=(
                "Sells first, then buys — the buys are funded by them. Place each "
                "at your own broker and mark it done; your holdings update "
                "automatically. Nothing is placed for you."
            ),
        )


#: Plain-English names for the pool's member states, as a reason for the user.
_STATE_REASON = {
    "quota": "used up its free daily limit",
    "rate_limited": "is rate-limited",
    "model_gone": "has a model that no longer exists",
    "bad_key": "rejected its API key",
    "transport": "timed out",
}


def _wait_phrase(seconds: int) -> str:
    if seconds >= 3600:
        return f"~{round(seconds / 3600)}h"
    if seconds >= 60:
        return f"~{round(seconds / 60)} min"
    return "under a minute"


def ai_offline_note(
    source: str,
    verdicts: list[dict],
    *,
    requested: bool,
    configured: bool,
    pool_status=None,
) -> str | None:
    """One line saying *why* a plan is not all AI, or None when it is.

    "The model was unreachable" told the user nothing they could act on. The
    reason is usually knowable — a spent free tier, a retired model, a server
    with no key — and each has a different fix (wait, update config, add a key).
    """
    if source == "ai" or not verdicts:
        return None
    if not requested:
        return "AI was switched off for this plan."
    if not configured:
        return "AI is not configured on the server (no LLM API key set)."

    members: list[dict] = []
    if callable(pool_status):
        try:
            members = list(pool_status())
        except Exception:  # noqa: BLE001 — a diagnostic must not fail the plan
            members = []
    down = [m for m in members if m.get("state") != "ok"]

    if members and len(down) == len(members):
        reasons: dict[str, int] = {}
        for m in down:
            reason = _STATE_REASON.get(str(m.get("state")), "is unavailable")
            reasons[reason] = reasons.get(reason, 0) + 1
        why = "; ".join(
            f"{n} {'provider' if n == 1 else 'providers'} {r}" for r, n in reasons.items()
        )
        waits = [int(m.get("retry_in") or 0) for m in down if m.get("state") != "bad_key"]
        when = f" Next one frees up in {_wait_phrase(min(waits))}." if waits else ""
        return f"Every AI provider is unavailable ({why}).{when}"

    if source == "mixed":
        rules = sum(1 for v in verdicts if v.get("source") != "ai")
        return (
            f"AI providers were busy; {rules} of {len(verdicts)} verdicts are rules-based. "
            "Rebuilding in a minute usually gets more AI answers."
        )
    return "The AI providers did not answer in time, so every verdict is rules-based. Try again in a minute."
