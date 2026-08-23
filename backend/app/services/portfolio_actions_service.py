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

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.committee import HeldContext, run_committee_many
from app.ai.llm.base import LLMProvider
from app.analytics.indicators import Indicators, compute_indicators
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ProposedOrder
from app.domain.enums import OrderSide
from app.integrations.marketdata.provider import MarketDataProvider
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
    ProposalOut,
    Sizing,
)
from app.services.deploy_service import CONCENTRATION_CAP, DeployService
from app.services.errors import ValidationFailed
from app.services.invest_service import InvestService, order_out
from app.services.market_data_service import MarketDataService

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

# Cash kept back rather than deployed to the last cent — an account with no cash
# at all cannot act on anything next month without selling first.
CASH_FLOOR_PCT = 0.02
MAX_RESERVE_SHARE = 0.25

# A buy should move the needle. Spreading a small budget evenly over every name
# the committee likes produces a page of $20 orders that change nothing and cost
# an afternoon to place — so the number of buys is bounded by what the budget
# can fund at this size, and conviction decides who makes the cut.
MEANINGFUL_TRADE_PCT = 0.03


class PortfolioActionsService:
    def __init__(
        self,
        session: AsyncSession,
        provider: MarketDataProvider,
        llm: LLMProvider,
    ) -> None:
        self.session = session
        self.provider = provider
        self.llm = llm
        self.market_data = MarketDataService(session, provider)
        self.holdings = HoldingRepository(session)
        self.proposals = ProposedOrderRepository(session)
        self.invest = InvestService(session, provider)
        # Candidate ranking, quoting and the scan universe are the cash
        # planner's problem already solved; this composes it rather than
        # growing a second copy that drifts out of step.
        self.deploy = DeployService(session, provider, llm)

    # --- planning (read-only: persists nothing) ---
    async def plan(self, user_id: uuid.UUID, body: ActionPlanIn) -> ActionPlanOut:
        rows = await self.holdings.list_for_user(user_id)
        if not rows:
            raise ValidationFailed(
                "Add your holdings — or sync your linked brokerage — before asking "
                "what to buy or sell."
            )

        # Price the book before valuing it: a position synced from the broker
        # minutes ago has no cached bars yet, and an unpriced holding would drop
        # out of the portfolio silently — taking any decision about it with it.
        await self.market_data.sync_bars([h.symbol for h in rows])
        await self.session.commit()

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

        held_results, held_prices, held_names = await self._judge_held(held, passed, use_ai)
        candidates, considered = await self._judge_candidates(user_id, body, passed, use_ai)
        # Each verdict degrades on its own, so "the AI ran" is not a property of
        # the request — it is a count of which ones actually came back. Saying
        # "ai" over a plan that is mostly rules would overstate it.
        source = self._source([*held_results.values(), *candidates])

        actions, holds, cash_after, unfunded = self._size(
            body=body,
            equity=equity,
            cash=cash,
            held=held,
            held_results=held_results,
            held_prices=held_prices,
            held_names=held_names,
            candidates=candidates,
            passed=passed,
        )

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
            note=self._note(actions, holds, cash_after, body, unfunded),
        )

    # --- stage 1: what you already own ---
    async def _judge_held(
        self, held: dict[str, dict], passed: list[DeployPassedOut], use_ai: bool
    ) -> tuple[dict[str, dict], dict[str, float], dict[str, str | None]]:
        """Committee verdict on every held position.

        Held positions are never shortlisted away: a plan that silently ignores
        something you own is worse than no plan.
        """
        symbols = list(held)
        if not symbols:
            return {}, {}, {}

        # Bars were synced by `plan` before the portfolio was valued.
        series = await self.market_data.get_close_series(symbols)
        scored: list[tuple[float, str, Indicators, str]] = []
        for symbol in symbols:
            closes = [c for _, c in series.get(symbol, [])]
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
        passed.extend(unpriced)
        if not quoted:
            return {}, {}, {}

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
        results = await run_committee_many(
            [(sym, quote, ind) for sym, quote, ind, _ in quoted],
            self.llm,
            use_ai=use_ai,
            held=contexts,
        )
        return (
            {r["symbol"]: r for r in results},
            {sym: price for sym, _, _, price in quoted},
            {sym: quote.get("name") for sym, quote, _, _ in quoted},
        )

    # --- stage 2: what you could own instead ---
    async def _judge_candidates(
        self,
        user_id: uuid.UUID,
        body: ActionPlanIn,
        passed: list[DeployPassedOut],
        use_ai: bool,
    ) -> tuple[list[dict], int]:
        """Rank the unheld universe for free, then spend calls on the top few."""
        if body.max_new_positions == 0:
            return [], 0

        pool = await self.deploy._candidate_pool(user_id)
        if not pool:
            return [], 0

        await self.market_data.sync_bars(pool)
        await self.session.commit()

        ranked, unpriced = await self.deploy._rank(pool)
        passed.extend(unpriced)
        if not ranked:
            return [], len(pool)

        quoted, no_quote = await self.deploy._quote(ranked[: body.max_candidates])
        passed.extend(no_quote)
        if not quoted:
            return [], len(pool)

        results = await run_committee_many(
            [(sym, quote, ind) for sym, quote, ind, _ in quoted], self.llm, use_ai=use_ai
        )
        prices = {sym: price for sym, _, _, price in quoted}
        names = {sym: quote.get("name") for sym, quote, _, _ in quoted}
        enriched = []
        for r in results:
            enriched.append({**r, "price_cad": prices[r["symbol"]], "name": names.get(r["symbol"])})
        return enriched, len(pool)

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
    ) -> tuple[list[ActionOut], list[ActionHoldOut], float, bool]:
        sells: list[ActionOut] = []
        holds: list[ActionHoldOut] = []
        adds: list[dict] = []
        keep: list[tuple[str, dict, dict, float]] = []  # symbol, position, result, price
        # Value freed by the sells, tracked per symbol so a name being trimmed
        # is never topped up in the same plan.
        trimmed: dict[str, float] = {}

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
                fraction = 1.0 if confidence >= FULL_EXIT_CONFIDENCE else PARTIAL_TRIM_FRACTION
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
                }
            )

        # Never fund a buy on a weaker signal than the sell paying for it. When
        # the model answers for some tickers and not others — free-tier capacity
        # comes and goes mid-plan — the naive result is selling what the
        # committee scrutinized to buy what only the rules saw. The proceeds sit
        # in cash instead, which is a position; a switch justified by the weaker
        # of two signals is not.
        judged_by_ai = any(
            str(held_results.get(sell.symbol, {}).get("source")) == "ai" for sell in sells
        )
        unfunded = False
        if judged_by_ai:
            kept = [a for a in adds if a.get("verdict_source") == "ai"]
            unfunded = len(kept) < len(adds)
            adds = kept

        # --- concentration overlay ---
        # A name can be a fine investment and still be too much of the account,
        # so this runs regardless of the verdict — but only when the proceeds
        # have somewhere else to go. Selling a position down to sit in cash is
        # not diversification, and a single broad ETF at 100% is a deliberate
        # strategy rather than a mistake to correct.
        for symbol, position, result, price in keep:
            weight = position["weight"]
            excess = max(0.0, (weight - CONCENTRATION_CAP) * equity)
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

        purchases, unaffordable = self._allocate_buys(adds, spendable, equity, body.sizing)
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
        return sells + purchases, holds, cash_after, unfunded

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
    ) -> tuple[list[ActionOut], list[dict]]:
        """Split the budget across the buys by conviction, capped per name.

        The cap is on the *resulting* weight, not the purchase: topping up
        something already large is exactly how a portfolio drifts into a single
        bet, and the committee liking a name is not a reason to allow it.

        Under share sizing the allocation is rounded down to whole shares, so a
        name whose share price exceeds its slice cannot be funded at all — an
        allocation of $94 against a $1,053 share is not a small buy, it is no
        buy. Those come back in the second list to be reported rather than
        proposed as an order nobody can place.
        """
        if not adds or spendable < MIN_TRADE_CAD:
            return [], []

        # Highest conviction first; ties go to the smaller position, since
        # adding to what you already hold most of is the one outcome nobody is
        # asking for.
        adds = sorted(adds, key=lambda a: (-a["confidence"], a["current_value"]))
        floor = max(MIN_TRADE_CAD, equity * MEANINGFUL_TRADE_PCT)
        adds = adds[: max(1, int(spendable // floor))]

        weights = {a["symbol"]: max(a["confidence"], 0.01) for a in adds}
        total = sum(weights.values())
        purchases: list[ActionOut] = []
        unaffordable: list[dict] = []
        for a in adds:
            symbol = a["symbol"]
            allocation = spendable * (weights[symbol] / total)
            # Room left before this name breaches the concentration cap.
            room = max(0.0, CONCENTRATION_CAP * equity - a["current_value"])
            allocation = min(allocation, room)
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
        return purchases, unaffordable

    @staticmethod
    def _source(results: list[dict]) -> str:
        """ai | mixed | rules, from the verdicts that actually came back."""
        sources = {str(r.get("source") or "rules") for r in results}
        if not sources or sources == {"rules"}:
            return "rules"
        return "ai" if sources == {"ai"} else "mixed"

    @staticmethod
    def _note(
        actions: list[ActionOut],
        holds: list[ActionHoldOut],
        cash_after: float,
        body: ActionPlanIn,
        unfunded: bool = False,
    ) -> str:
        if not actions:
            kept = ", ".join(h.symbol for h in holds)
            return (
                "No changes worth making. The committee would keep "
                f"{kept or 'what you hold'} as it is, and nothing on the shortlist "
                "beat what you already own. Doing nothing is a position."
            )
        sells = [a for a in actions if a.side == "sell"]
        buys = [a for a in actions if a.side == "buy"]
        parts = []
        if sells:
            parts.append("sell " + ", ".join(f"${a.amount:,.0f} of {a.symbol}" for a in sells))
        if buys:
            parts.append("put " + ", ".join(f"${a.amount:,.0f} into {a.symbol}" for a in buys))
        parked = (
            " The model was only partly reachable, so ideas it didn't get to judge "
            "were left unfunded and that money stays in cash."
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
            + sizing_note
            + " Accepting adds these to your trade checklist — nothing is placed "
            "until you approve each one."
        )

    # --- accepting (writes PROPOSED rows — never APPROVED) ---
    async def accept(self, user_id: uuid.UUID, body: ActionAcceptIn) -> ProposalOut:
        await self.proposals.expire_open_proposals(user_id)
        batch_id = uuid.uuid4()
        dollars = body.sizing == "dollars"

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
