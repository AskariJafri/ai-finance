"""Cash deployment: "I have $X to invest — what do I buy?"

The one question the rest of the Invest module can't answer. `propose_rebalance`
needs target weights already set; the committee analyses one ticker with no
notion of a budget. This turns an amount into a concrete, executable buy list.

Selection is two-stage, cheap before expensive, because each committee ticker
costs an LLM request and the free tier meters those per day:

  1. rank the whole candidate pool with `signal_for` — pure arithmetic over
     cached bars, no LLM, so it costs nothing to run over everything;
  2. put only the top few through the AI committee, concurrently.

The pool is `DEPLOY_UNIVERSE` (below) plus whatever the user watches. It is a
fixed list rather than "every listed ticker" for a structural reason: the
market-data provider exposes quotes and bars per symbol and has no screener, so
"all tickers" would mean tens of thousands of round trips. Stage 1 is free
though, so the list can be as wide as the bar cache can carry — widen it there.

Guardrails, same as everywhere else in this module: every figure comes from
market data (the LLM only interprets), sizing is whole shares because a
fractional qty cannot be routed to a broker, and the engine only ever PROPOSES —
nothing here may set APPROVED.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.committee import run_committee_many, source_of
from app.ai.llm.base import LLMProvider
from app.ai.llm.budget import llm_budget
from app.analytics.indicators import Indicators, compute_indicators
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ProposedOrder
from app.domain.enums import OrderSide
from app.integrations.marketdata.provider import MarketDataError, MarketDataProvider
from app.repositories.holdings import HoldingRepository
from app.repositories.proposed_orders import ProposedOrderRepository
from app.repositories.watchlist import WatchlistRepository
from app.schemas.common import cents_to_dollars
from app.schemas.invest import (
    DeployAcceptIn,
    DeployPassedOut,
    DeployPickOut,
    DeployPlanIn,
    DeployPlanOut,
    ProposalOut,
    Sizing,
)
from app.services.autotrader_service import DEFAULT_UNIVERSE, rules_verdict, signal_for
from app.services.errors import ValidationFailed
from app.services.invest_service import order_out
from app.services.market_context_service import MarketContextService, research_out
from app.services.market_data_service import MarketDataService

logger = get_logger(__name__)

# The scan pool. Canadian-listed first (the user banks in CAD, and a .TO holding
# avoids the FX spread on every buy), then US mega caps and core ETFs reachable
# from any Canadian brokerage. Yahoo tickers — a symbol that stops resolving
# degrades to one "unpriced" line, so a stale entry here is not a failure.
# fmt: off
# Grouped by sector on purpose — one symbol per line would bury that, and the
# grouping is what makes an omission obvious when editing the list.
DEPLOY_UNIVERSE = [
    # One-decision ETFs — for most people this is the whole right answer.
    "XEQT.TO", "VEQT.TO", "VGRO.TO", "XGRO.TO", "VBAL.TO",
    "VFV.TO", "ZSP.TO", "XUU.TO", "XAW.TO", "XIC.TO", "VCN.TO",
    "ZAG.TO", "XBB.TO",
    # Canadian large caps, by sector.
    "RY.TO", "TD.TO", "BNS.TO", "BMO.TO", "CM.TO", "NA.TO",
    "MFC.TO", "SLF.TO", "IFC.TO", "BN.TO",
    "ENB.TO", "TRP.TO", "CNQ.TO", "SU.TO", "PPL.TO",
    "CNR.TO", "CP.TO", "WCN.TO", "TFII.TO",
    "BCE.TO", "T.TO", "RCI-B.TO",
    "SHOP.TO", "CSU.TO", "ATD.TO", "L.TO", "MRU.TO", "DOL.TO",
    "FTS.TO", "EMA.TO", "AQN.TO",
    "AEM.TO", "ABX.TO", "WPM.TO", "NTR.TO", "TECK-B.TO", "CCO.TO",
    # US core ETFs.
    "SPY", "QQQ", "VTI", "SCHD", "BND",
    # US mega caps.
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA",
    "BRK-B", "JPM", "V", "MA", "COST", "WMT", "HD", "MCD",
    "UNH", "JNJ", "LLY", "PG", "KO", "PEP", "XOM", "NFLX",
]
# fmt: on

# Indicators reach back six months (`ret_6m`); two years gives them room and
# keeps a cold scan of the universe cheap. Backtests still pull full history.
SCAN_HISTORY_DAYS = 730

# A name may run past its conviction target to soak up leftover cash, but not
# past this share of the amount — otherwise the cheapest ETF absorbs the whole
# plan and "split across tickers" quietly becomes one ticker.
CONCENTRATION_CAP = 0.40

# How far past its conviction slice one share may cost and still earn the name a
# place in the plan. Without it a $600 share could never join a $1,000 two-way
# split; much above it and a low-conviction name buys in on price alone.
SEED_TOLERANCE = 1.5

# The remainder sweep buys one share per step, so it is bounded rather than
# left to iterate on a large amount against a penny stock. Whatever is still
# unspent when it runs out simply stays in cash.
MAX_SWEEP_STEPS = 500

# `passed` is shown in the app, so it stays a readable list of near-misses
# rather than a dump of every symbol the scan touched. `considered` carries the
# real scan size.
MAX_PASSED_REPORTED = 20
NEAR_MISSES_REPORTED = 3

# Ordering for that list: what the committee actually judged first, what merely
# ranked low last.
_PASSED_PRIORITY = {"too-expensive": 0, "SKIP": 1, "HOLD": 1, "not-shortlisted": 3, "unpriced": 4}


class DeployService:
    def __init__(
        self,
        session: AsyncSession,
        provider: MarketDataProvider,
        llm: LLMProvider,
        context: MarketContextService | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.llm = llm
        # Optional keyword so every existing construction keeps working.
        self.context = context
        self.market_data = MarketDataService(session, provider)
        self.holdings = HoldingRepository(session)
        self.watchlist = WatchlistRepository(session)
        self.proposals = ProposedOrderRepository(session)
        # Set by a caller that has resolved the user's brokerage link (see
        # live_quotes.broker_quote_fetcher). None means market data only.
        self.live_quotes = None

    # --- planning (read-only: persists nothing) ---
    async def plan(self, user_id: uuid.UUID, body: DeployPlanIn) -> DeployPlanOut:
        pool = await self._candidate_pool(user_id)
        if not pool:
            raise ValidationFailed(
                "No candidates to consider — add a few tickers to your watchlist first."
            )

        # One sync for the whole pool. Bad tickers land in `failed` rather than
        # raising, so an unknown symbol costs us that candidate, not the request.
        await self.market_data.sync_bars(
            pool, since=date.today() - timedelta(days=SCAN_HISTORY_DAYS)
        )
        await self.session.commit()

        ranked, passed = await self._rank(pool)
        if not ranked:
            return self._empty_plan(
                body.amount,
                passed,
                len(pool),
                "rules",
                "None of the candidates had enough price history to judge yet. "
                "Try again once market data has synced.",
            )

        finalists = ranked[: body.max_candidates]
        for _, symbol, _, _ in ranked[body.max_candidates :][:NEAR_MISSES_REPORTED]:
            passed.append(
                DeployPassedOut(
                    symbol=symbol,
                    action="not-shortlisted",
                    reason="ranked just below the shortlist on trend and momentum.",
                )
            )

        quoted, unpriced = await self._quote(finalists)
        passed.extend(unpriced)
        if not quoted:
            return self._empty_plan(
                body.amount, passed, len(pool), "rules", "No candidate could be priced."
            )

        use_ai = body.use_ai and settings.ai_available and await self._llm_ready()

        # Research runs here — after quoting, not after ranking. Quoting drops
        # candidates as unpriced, and fetching news for a name we are about to
        # discard is a metered call spent on nothing. This is the whole tiering:
        # the pool above may be ~65 names; `quoted` is at most max_candidates.
        market = None
        if use_ai and self.context is not None:
            market = await self.context.context([sym for sym, _, _, _ in quoted])
            await self.session.commit()

        # One deadline shared by the whole fan-out. Opened here rather than
        # per call because the tickers run concurrently: a per-call allowance
        # would let waves of them each wait the full amount, and the bound the
        # user actually cares about is when the request returns.
        with llm_budget(
            wait=settings.llm_wait_plan_seconds,
            max_pass_sleep=settings.llm_max_pass_sleep_seconds,
            max_output_tokens=settings.llm_committee_max_tokens,
        ):
            results = await run_committee_many(
                [(sym, quote, ind) for sym, quote, ind, _ in quoted],
                self.llm,
                use_ai=use_ai,
                rules={
                    sym: rules_verdict(ind, held=False, symbol=sym) for sym, _, ind, _ in quoted
                },
                market=market,
            )
        # Derived from the verdicts that actually came back, not from the gate
        # decided before any call was made — a scan whose calls all 429 must not
        # report itself as an AI plan.
        source = source_of(results)

        prices = {sym: price for sym, _, _, price in quoted}
        names = {sym: quote.get("name") for sym, quote, _, _ in quoted}
        plan = self._size(
            body.amount,
            results,
            prices,
            names,
            passed,
            len(pool),
            source,
            body.max_positions,
            body.sizing,
        )
        # Attached here rather than threaded through _size/_size_by_dollars/
        # _empty_plan: the research context is a property of the request, not
        # of the sizing arithmetic.
        plan.research = research_out(market)
        return plan

    async def _candidate_pool(self, user_id: uuid.UUID) -> list[str]:
        """Watchlist first, then the whole scan universe. Anything already held
        is excluded — this plans new positions, not top-ups."""
        watched = [i.symbol.upper() for i in await self.watchlist.list_for_user(user_id)]
        held = {h.symbol.upper() for h in await self.holdings.list_for_user(user_id)}
        pool: list[str] = []
        for symbol in [*watched, *DEPLOY_UNIVERSE, *DEFAULT_UNIVERSE]:
            if symbol not in pool and symbol not in held:
                pool.append(symbol)
        return pool

    async def _rank(
        self, pool: list[str], *, before: date | None = None
    ) -> tuple[list[tuple[float, str, Indicators, str]], list[DeployPassedOut]]:
        """Stage 1 — score every candidate on cached bars. Zero LLM calls.

        ``before`` restricts the signal to sessions completed before that day,
        so a ranking does not drift with today's still-moving bar."""
        series = await self.market_data.get_close_series(pool)
        ranked: list[tuple[float, str, Indicators, str]] = []
        passed: list[DeployPassedOut] = []

        for symbol in pool:
            bars = series.get(symbol, [])
            closes = [c for d, c in bars if before is None or d < before] or [c for _, c in bars]
            try:
                ind = compute_indicators(closes)
            except ValueError:
                passed.append(
                    DeployPassedOut(
                        symbol=symbol,
                        action="unpriced",
                        reason="not enough price history to judge.",
                    )
                )
                continue
            score, action, _ = signal_for(ind, held=False, symbol=symbol)
            ranked.append((score, symbol, ind, action))

        # Anything the pre-committed rules would actually buy outranks a
        # merely high-scoring "no edge" name, so the committee spends its
        # (metered) calls on real candidates first.
        ranked.sort(key=lambda r: (r[3] == "buy", r[0]), reverse=True)
        return ranked, passed

    async def _quote(
        self, finalists: list[tuple[float, str, Indicators, str]]
    ) -> tuple[list[tuple[str, dict, Indicators, float]], list[DeployPassedOut]]:
        """Live quote + CAD price for each shortlisted candidate.

        Quotes are fetched concurrently; the FX rate is read once for the whole
        batch rather than per symbol (`sync_bars` always caches USDCAD=X, so it
        is warm by now). Nothing here touches the session concurrently.
        """
        symbols = [symbol for _, symbol, _, _ in finalists]
        indicators = {symbol: ind for _, symbol, ind, _ in finalists}

        # The user's broker first, when a planner has wired one in: a real-time
        # quote under the broker's licence. Whatever it can't price falls
        # through to the market-data provider, fetched fresh.
        broker: dict = {}
        if self.live_quotes is not None:
            try:
                broker = await self.live_quotes(symbols)
            except Exception as exc:  # noqa: BLE001 — fall back, never fail the plan
                logger.warning("Broker quotes failed, using market data: %s", exc)

        async def one(symbol: str):
            if symbol.upper() in broker:
                return symbol, broker[symbol.upper()], None
            try:
                return symbol, await self.provider.get_quote(symbol), None
            except MarketDataError as exc:
                return symbol, None, exc

        results = await asyncio.gather(*(one(s) for s in symbols))
        fx = await self.market_data.latest_fx_rate()

        quoted: list[tuple[str, dict, Indicators, float]] = []
        unpriced: list[DeployPassedOut] = []
        for symbol, quote, error in results:
            if error is not None or quote is None:
                unpriced.append(
                    DeployPassedOut(
                        symbol=symbol, action="unpriced", reason="no live quote available."
                    )
                )
                continue
            # The quote's own currency, not a guess from a `.TO` suffix.
            price_cad = (
                round(quote.price, 4)
                if quote.currency == "CAD"
                else (round(quote.price * fx, 4) if fx else None)
            )
            if not price_cad:
                unpriced.append(
                    DeployPassedOut(
                        symbol=symbol,
                        action="unpriced",
                        reason="could not convert its price to CAD.",
                    )
                )
                continue
            quoted.append(
                (
                    symbol,
                    {
                        "price": round(quote.price, 4),
                        "currency": quote.currency,
                        "name": quote.name,
                        "source": getattr(quote, "source", "yahoo"),
                        # The exchange's timestamp when the provider gives
                        # one; otherwise the moment we fetched it.
                        "as_of": (getattr(quote, "as_of", None) or datetime.now(UTC)).isoformat(),
                    },
                    indicators[symbol],
                    price_cad,
                )
            )
        return quoted, unpriced

    async def _llm_ready(self) -> bool:
        """Is it worth *attempting* the AI at all for this plan?

        Deliberately optimistic. This used to ask "is anything up?" and one
        False turned off the model for every ticker in the scan — so a single
        unlucky probe produced a wholly rules-based plan even when four of five
        endpoints were healthy.

        It is now a floor, not a gate: `run_committee_many` already degrades
        per ticker, and a member with a spent quota already declines in
        microseconds without touching the network. So the only thing worth
        checking is whether the AI is *provably* gone — every member benched or
        known dry. Anything short of that is worth trying and letting the
        per-ticker fallback handle.
        """
        live = getattr(self.llm, "live_member_count", None)
        if callable(live):
            try:
                return live() > 0
            except Exception as exc:  # noqa: BLE001 — never fail a plan on a probe
                logger.warning("LLM liveness check failed, assuming up: %s", exc)
                return True
        try:
            return await self.llm.is_available()
        except Exception as exc:  # noqa: BLE001 — unreachable model is not an error
            logger.warning("LLM availability check failed, using rules: %s", exc)
            return False

    def _size(
        self,
        amount: float,
        results: list[dict],
        prices: dict[str, float],
        names: dict[str, str | None],
        passed: list[DeployPassedOut],
        considered: int,
        source: str,
        max_positions: int,
        sizing: Sizing = "shares",
    ) -> DeployPlanOut:
        """Allocate the budget across BUY verdicts, weighted by confidence.

        Two sizings. Under ``shares`` the plan is whole shares, because
        `BrokerageService.approve_and_submit` refuses to route a fractional qty
        — anything else produces orders that can't execute. Under ``dollars``
        the conviction weights are spent as-is, which is what a fractional-share
        broker takes and the only sizing that does anything useful on an account
        too small to buy one share of what it wants.
        """
        winners = [r for r in results if r.get("action") == "BUY"]
        for r in results:
            if r.get("action") != "BUY":
                passed.append(
                    DeployPassedOut(
                        symbol=r["symbol"],
                        action=r.get("action", "SKIP"),
                        confidence=r.get("confidence"),
                        reason=r.get("summary") or "the committee passed on it.",
                    )
                )

        if not winners:
            return self._empty_plan(
                amount,
                passed,
                considered,
                source,
                "The committee didn't find a buy it liked among these candidates. "
                "Holding cash is a position too.",
            )

        # Conviction order, then cut to the number of positions asked for. A
        # small amount spread over many names buys nothing at all, so this is
        # what keeps the plan executable rather than merely diversified.
        winners.sort(key=lambda r: float(r.get("confidence") or 0), reverse=True)
        for r in winners[max_positions:]:
            passed.append(
                DeployPassedOut(
                    symbol=r["symbol"],
                    action="not-shortlisted",
                    confidence=r.get("confidence"),
                    reason=(
                        f"a buy, but ranked outside the {max_positions}-position "
                        "limit for this plan."
                    ),
                )
            )
        winners = winners[:max_positions]

        # Confidence is floored so a zero-confidence BUY still gets a slice
        # rather than dividing by zero.
        confidences = {r["symbol"]: max(float(r.get("confidence") or 0), 0.01) for r in winners}
        total_conf = sum(confidences.values())
        targets = {symbol: conf / total_conf for symbol, conf in confidences.items()}

        if sizing == "dollars":
            return self._size_by_dollars(
                amount, winners, targets, prices, names, passed, considered, source
            )

        qty = self._allocate(amount, targets, prices)

        picks: list[DeployPickOut] = []
        for r in winners:
            symbol = r["symbol"]
            price = prices[symbol]
            if qty[symbol] < 1:
                passed.append(
                    DeployPassedOut(
                        symbol=symbol,
                        action="too-expensive",
                        confidence=r.get("confidence"),
                        reason=(
                            f"one share is ${price:,.2f} CAD — more than was left "
                            f"once the rest of the plan was funded."
                        ),
                    )
                )
                continue
            est_value = round(qty[symbol] * price, 2)
            picks.append(
                DeployPickOut(
                    symbol=symbol,
                    name=names.get(symbol),
                    qty=qty[symbol],
                    price=price,
                    # The share of the amount this pick actually got, not the
                    # target it was aiming at — whole shares rarely hit it.
                    weight=round(est_value / amount, 4),
                    est_value=est_value,
                    confidence=round(float(r.get("confidence") or 0), 2),
                    rationale=str(r.get("summary") or "").strip()[:500],
                )
            )

        picks.sort(key=lambda p: p.est_value, reverse=True)
        invested = round(sum(p.est_value for p in picks), 2)
        remaining = round(amount - invested, 2)

        if not picks:
            cheapest = min(prices[r["symbol"]] for r in winners)
            return self._empty_plan(
                amount,
                passed,
                considered,
                source,
                f"${amount:,.2f} doesn't cover a single share of anything the "
                f"committee liked — the cheapest was ${cheapest:,.2f}. Try a "
                "larger amount, or add a lower-priced ETF to your watchlist.",
            )

        split = ", ".join(
            f"{p.qty}× {p.symbol} ≈ ${p.est_value:,.0f} ({p.weight * 100:.0f}%)" for p in picks
        )
        note = (
            f"Split ${invested:,.2f} of ${amount:,.2f} across {len(picks)} position(s): "
            f"{split}. ${remaining:,.2f} stays in cash (whole shares only). "
            "Accepting adds these to your trade checklist — nothing is placed until "
            "you approve each one."
        )
        return DeployPlanOut(
            amount=round(amount, 2),
            sizing="shares",
            picks=picks,
            passed=self._trim_passed(passed),
            invested=invested,
            cash_remaining=remaining,
            considered=considered,
            source=source,
            note=note,
        )

    def _size_by_dollars(
        self,
        amount: float,
        winners: list[dict],
        targets: dict[str, float],
        prices: dict[str, float],
        names: dict[str, str | None],
        passed: list[DeployPassedOut],
        considered: int,
        source: str,
    ) -> DeployPlanOut:
        """Spend the conviction weights directly, as dollar amounts.

        No seed/fill/sweep: without the whole-share constraint the target
        weights are simply achievable, so the allocation *is* the answer. The
        concentration cap still applies — conviction alone should not put 80% of
        the money in one name — and whatever the cap trims is redistributed over
        the rest rather than left in cash.
        """
        capped: dict[str, float] = {}
        spill = 0.0
        for symbol, weight in targets.items():
            share = min(weight, CONCENTRATION_CAP)
            spill += weight - share
            capped[symbol] = share

        # Redistribute what the cap took, proportionally among names with room.
        for _ in range(3):
            if spill <= 1e-9:
                break
            room = {s: CONCENTRATION_CAP - w for s, w in capped.items() if w < CONCENTRATION_CAP}
            total_room = sum(room.values())
            if total_room <= 1e-9:
                break
            moved = min(spill, total_room)
            for symbol, available in room.items():
                capped[symbol] += moved * (available / total_room)
            spill -= moved

        picks: list[DeployPickOut] = []
        for r in winners:
            symbol = r["symbol"]
            allocation = round(amount * capped[symbol], 2)
            if allocation < 1.0:
                passed.append(
                    DeployPassedOut(
                        symbol=symbol,
                        action="too-expensive",
                        confidence=r.get("confidence"),
                        reason="its slice of the amount came to under a dollar.",
                    )
                )
                continue
            price = prices[symbol]
            picks.append(
                DeployPickOut(
                    symbol=symbol,
                    name=names.get(symbol),
                    qty=round(allocation / price, 4),
                    price=price,
                    weight=round(allocation / amount, 4),
                    est_value=allocation,
                    confidence=round(float(r.get("confidence") or 0), 2),
                    rationale=str(r.get("summary") or "").strip()[:500],
                )
            )

        if not picks:
            return self._empty_plan(
                amount,
                passed,
                considered,
                source,
                f"${amount:,.2f} is too small to split across the names the "
                "committee liked. Try a larger amount.",
                sizing="dollars",
            )

        picks.sort(key=lambda p: p.est_value, reverse=True)

        # Rounding each slice to the cent can land a cent or two either side of
        # the amount. Absorb the difference into the largest position so the
        # plan never proposes spending more than the user actually has.
        drift = round(round(sum(p.est_value for p in picks), 2) - amount, 2)
        if drift and picks:
            largest = picks[0]
            adjusted = round(largest.est_value - drift, 2)
            picks[0] = largest.model_copy(
                update={
                    "est_value": adjusted,
                    "qty": round(adjusted / largest.price, 4),
                    "weight": round(adjusted / amount, 4),
                }
            )

        invested = round(sum(p.est_value for p in picks), 2)
        split = ", ".join(
            f"${p.est_value:,.0f} into {p.symbol} ({p.weight * 100:.0f}%)" for p in picks
        )
        note = (
            f"Split ${invested:,.2f} of ${amount:,.2f} across {len(picks)} position(s): "
            f"{split}. Dollar amounts, so this needs a broker that sells "
            "fractional shares — Wealthsimple and Questrade both do. Accepting "
            "adds these to your trade checklist — nothing is placed until you "
            "approve each one."
        )
        return DeployPlanOut(
            amount=round(amount, 2),
            sizing="dollars",
            picks=picks,
            passed=self._trim_passed(passed),
            invested=invested,
            cash_remaining=round(amount - invested, 2),
            considered=considered,
            source=source,
            note=note,
        )

    @staticmethod
    def _allocate(
        amount: float, targets: dict[str, float], prices: dict[str, float]
    ) -> dict[str, int]:
        """Turn conviction weights into whole-share counts.

        Three passes, and the order between the first two is the whole trick:

          1. seed one share of each name that deserves a place, dearest first;
          2. bulk-fill each name up to its conviction slice;
          3. sweep what rounding left behind into whoever is furthest below
             target.

        Seeding has to come first because a cheap name will otherwise eat the
        budget a dear one needed — allocate $1,000 proportionally across a $10
        stock and a $600 stock and the $10 side takes its $500 slice, leaving
        $400 against a $600 share that then never gets bought.

        Pass 3 is what makes small amounts work at all: at $500 across four
        names no single slice covers a $200 share, so flooring alone returns
        nothing and leaves the whole amount in cash — which is what this engine
        used to do.
        """
        qty = {symbol: 0 for symbol in targets}

        # A name whose single share costs more than the whole amount can't take
        # part at all. Drop it and renormalise, so its conviction funds the
        # names that can rather than sitting the difference out in cash.
        usable = {s: w for s, w in targets.items() if prices[s] <= amount}
        if not usable:
            return qty
        total_weight = sum(usable.values()) or 1.0
        weights = {s: w / total_weight for s, w in usable.items()}
        dearest_first = sorted(weights, key=lambda s: prices[s], reverse=True)
        spent = 0.0

        # 1. A share each, for names whose price is close enough to their slice
        #    to have earned it.
        for symbol in dearest_first:
            price = prices[symbol]
            if price > amount - spent:
                continue
            if price > weights[symbol] * amount * SEED_TOLERANCE:
                continue
            qty[symbol] = 1
            spent += price

        # 2. Up to each name's conviction slice, still dearest first.
        for symbol in dearest_first:
            price = prices[symbol]
            wanted = int((weights[symbol] * amount) // price) - qty[symbol]
            add = max(0, min(wanted, int((amount - spent) // price)))
            qty[symbol] += add
            spent += add * price

        # 3. The remainder.
        for _ in range(MAX_SWEEP_STEPS):
            leftover = amount - spent
            best: str | None = None
            best_deficit = 0.0
            for symbol, weight in weights.items():
                price = prices[symbol]
                if price > leftover:
                    continue
                # A name may overshoot its target to use up cash, but not past
                # the concentration cap. Its own target always wins over the
                # cap, so a one- or two-name split is never blocked by it.
                cap = max(CONCENTRATION_CAP, weight) * amount
                if (qty[symbol] + 1) * price > cap:
                    continue
                deficit = weight * amount - qty[symbol] * price
                if best is None or deficit > best_deficit:
                    best, best_deficit = symbol, deficit
            if best is None:
                break  # nothing affordable still under its cap — rest stays cash
            qty[best] += 1
            spent += prices[best]
        return qty

    @staticmethod
    def _trim_passed(passed: list[DeployPassedOut]) -> list[DeployPassedOut]:
        """Committee verdicts first, low-ranked also-rans last, then cap it.

        A wide scan produces far more also-rans than anyone wants to read, and
        `considered` already reports how many were looked at.
        """
        ordered = sorted(passed, key=lambda p: _PASSED_PRIORITY.get(p.action, 2))
        return ordered[:MAX_PASSED_REPORTED]

    @classmethod
    def _empty_plan(
        cls,
        amount: float,
        passed: list[DeployPassedOut],
        considered: int,
        source: str,
        note: str,
        sizing: Sizing = "shares",
    ) -> DeployPlanOut:
        return DeployPlanOut(
            amount=round(amount, 2),
            sizing=sizing,
            picks=[],
            passed=cls._trim_passed(passed),
            invested=0.0,
            cash_remaining=round(amount, 2),
            considered=considered,
            source=source,
            note=note,
        )

    # --- accepting (writes PROPOSED rows — never APPROVED) ---
    async def accept(self, user_id: uuid.UUID, body: DeployAcceptIn) -> ProposalOut:
        batch_id = uuid.uuid4()

        # Dollar-sized orders carry the amount and no quantity: the amount is
        # what the user decided, and the share count it implies moves with the
        # price between now and the fill.
        dollars = body.sizing == "dollars"
        orders = [
            ProposedOrder(
                user_id=user_id,
                batch_id=batch_id,
                kind="deploy",
                symbol=o.symbol.strip().upper(),
                side=OrderSide.BUY,
                order_type="market",
                qty=None if dollars else float(o.qty),
                notional_cents=round(o.qty * o.price * 100) if dollars else None,
                est_value_cents=round(o.qty * o.price * 100),
                reason=(
                    o.reason.strip()
                    or (
                        f"Put ${o.qty * o.price:,.2f} CAD into {o.symbol.upper()} "
                        "from your cash plan."
                        if dollars
                        else f"Buy {o.qty:g} share(s) of {o.symbol.upper()} "
                        f"≈ ${o.qty * o.price:,.2f} CAD from your cash plan."
                    )
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

        total_buy = sum(cents_to_dollars(o.est_value_cents) for o in orders)
        return ProposalOut(
            batch_id=str(batch_id),
            orders=[order_out(o) for o in orders],
            total_buy=round(total_buy, 2),
            total_sell=0.0,
            note=(
                "Place these at your own broker and mark each one done — your "
                "holdings update automatically. Nothing is placed for you."
            ),
        )
