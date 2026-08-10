"""Cash deployment: "I have $X to invest — what do I buy?"

The one question the rest of the Invest module can't answer. `propose_rebalance`
needs target weights already set; the committee analyses one ticker with no
notion of a budget. This turns an amount into a concrete, executable buy list.

Selection is two-stage, cheap before expensive, because each committee ticker
costs an LLM request and the free tier meters those per day:

  1. rank the whole candidate pool with `signal_for` — pure arithmetic over
     cached bars, no LLM, so it costs nothing to run over everything;
  2. put only the top few through the AI committee, concurrently.

Guardrails, same as everywhere else in this module: every figure comes from
market data (the LLM only interprets), sizing is whole shares because a
fractional qty cannot be routed to a broker, and the engine only ever PROPOSES —
nothing here may set APPROVED.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.committee import run_committee_many
from app.ai.llm.base import LLMProvider
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
)
from app.services.autotrader_service import DEFAULT_UNIVERSE, signal_for
from app.services.errors import ValidationFailed
from app.services.invest_service import order_out
from app.services.market_data_service import MarketDataService

logger = get_logger(__name__)


class DeployService:
    def __init__(
        self, session: AsyncSession, provider: MarketDataProvider, llm: LLMProvider
    ) -> None:
        self.session = session
        self.provider = provider
        self.llm = llm
        self.market_data = MarketDataService(session, provider)
        self.holdings = HoldingRepository(session)
        self.watchlist = WatchlistRepository(session)
        self.proposals = ProposedOrderRepository(session)

    # --- planning (read-only: persists nothing) ---
    async def plan(self, user_id: uuid.UUID, body: DeployPlanIn) -> DeployPlanOut:
        pool = await self._candidate_pool(user_id)
        if not pool:
            raise ValidationFailed(
                "No candidates to consider — add a few tickers to your watchlist first."
            )

        # One sync for the whole pool. Bad tickers land in `failed` rather than
        # raising, so an unknown symbol costs us that candidate, not the request.
        await self.market_data.sync_bars(pool)
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
        for _, symbol, _ in ranked[body.max_candidates :]:
            passed.append(
                DeployPassedOut(
                    symbol=symbol,
                    action="not-shortlisted",
                    reason="ranked below the shortlist on trend and momentum.",
                )
            )

        quoted, unpriced = await self._quote(finalists)
        passed.extend(unpriced)
        if not quoted:
            return self._empty_plan(
                body.amount, passed, len(pool), "rules", "No candidate could be priced."
            )

        use_ai = body.use_ai and settings.ai_available and await self._llm_ready()
        results = await run_committee_many(
            [(sym, quote, ind) for sym, quote, ind, _ in quoted], self.llm, use_ai=use_ai
        )
        source = "ai" if use_ai else "rules"

        prices = {sym: price for sym, _, _, price in quoted}
        names = {sym: quote.get("name") for sym, quote, _, _ in quoted}
        return self._size(body.amount, results, prices, names, passed, len(pool), source)

    async def _candidate_pool(self, user_id: uuid.UUID) -> list[str]:
        """Watchlist first, topped up from the default universe. Anything already
        held is excluded — this plans new positions, not top-ups."""
        watched = [i.symbol.upper() for i in await self.watchlist.list_for_user(user_id)]
        held = {h.symbol.upper() for h in await self.holdings.list_for_user(user_id)}
        pool: list[str] = []
        for symbol in [*watched, *DEFAULT_UNIVERSE]:
            if symbol not in pool and symbol not in held:
                pool.append(symbol)
        return pool

    async def _rank(
        self, pool: list[str]
    ) -> tuple[list[tuple[float, str, Indicators]], list[DeployPassedOut]]:
        """Stage 1 — score every candidate on cached bars. Zero LLM calls."""
        series = await self.market_data.get_close_series(pool)
        ranked: list[tuple[float, str, Indicators]] = []
        passed: list[DeployPassedOut] = []

        for symbol in pool:
            closes = [c for _, c in series.get(symbol, [])]
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
            score, _, _ = signal_for(ind, held=False)
            ranked.append((score, symbol, ind))

        ranked.sort(key=lambda r: r[0], reverse=True)
        return ranked, passed

    async def _quote(
        self, finalists: list[tuple[float, str, Indicators]]
    ) -> tuple[list[tuple[str, dict, Indicators, float]], list[DeployPassedOut]]:
        """Live quote + CAD price for each shortlisted candidate."""
        quoted: list[tuple[str, dict, Indicators, float]] = []
        unpriced: list[DeployPassedOut] = []

        for _, symbol, ind in finalists:
            try:
                quote = await self.provider.get_quote(symbol)
            except MarketDataError:
                unpriced.append(
                    DeployPassedOut(
                        symbol=symbol, action="unpriced", reason="no live quote available."
                    )
                )
                continue
            price_cad = await self._to_cad(quote.price, quote.currency)
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
                    },
                    ind,
                    price_cad,
                )
            )
        return quoted, unpriced

    async def _to_cad(self, price: float, currency: str) -> float | None:
        """`sync_bars` always caches USDCAD=X, so the rate is warm by now.

        Uses the quote's own currency rather than guessing from a `.TO` suffix.
        """
        if currency == "CAD":
            return round(price, 4)
        fx = await self.market_data.latest_fx_rate()
        return round(price * fx, 4) if fx else None

    async def _llm_ready(self) -> bool:
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
    ) -> DeployPlanOut:
        """Allocate the budget across BUY verdicts, weighted by confidence.

        Whole shares only: `BrokerageService.approve_and_submit` refuses to route
        a fractional qty, so anything else produces orders that can't execute.
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

        # Confidence is floored so a zero-confidence BUY still gets a slice
        # rather than dividing by zero.
        confidences = {r["symbol"]: max(float(r.get("confidence") or 0), 0.01) for r in winners}
        total_conf = sum(confidences.values())

        picks: list[DeployPickOut] = []
        for r in winners:
            symbol = r["symbol"]
            weight = confidences[symbol] / total_conf
            price = prices[symbol]
            qty = int((amount * weight) // price)
            if qty < 1:
                passed.append(
                    DeployPassedOut(
                        symbol=symbol,
                        action="too-expensive",
                        confidence=r.get("confidence"),
                        reason=(
                            f"one share is ${price:,.2f} CAD, above its "
                            f"${amount * weight:,.2f} slice of this amount."
                        ),
                    )
                )
                continue
            picks.append(
                DeployPickOut(
                    symbol=symbol,
                    name=names.get(symbol),
                    qty=qty,
                    price=price,
                    est_value=round(qty * price, 2),
                    weight=round(weight, 4),
                    confidence=round(float(r.get("confidence") or 0), 2),
                    rationale=str(r.get("summary") or "").strip()[:500],
                )
            )

        picks.sort(key=lambda p: p.est_value, reverse=True)
        invested = round(sum(p.est_value for p in picks), 2)
        remaining = round(amount - invested, 2)

        if not picks:
            return self._empty_plan(
                amount,
                passed,
                considered,
                source,
                "Every pick costs more per share than its slice of this amount — "
                "try a larger amount.",
            )

        note = (
            f"{len(picks)} buy(s) using ${invested:,.2f} of ${amount:,.2f}; "
            f"${remaining:,.2f} stays in cash (whole shares only). "
            "Accepting adds these to your trade checklist — nothing is placed until "
            "you approve each one."
        )
        return DeployPlanOut(
            amount=round(amount, 2),
            picks=picks,
            passed=passed,
            invested=invested,
            cash_remaining=remaining,
            considered=considered,
            source=source,
            note=note,
        )

    @staticmethod
    def _empty_plan(
        amount: float,
        passed: list[DeployPassedOut],
        considered: int,
        source: str,
        note: str,
    ) -> DeployPlanOut:
        return DeployPlanOut(
            amount=round(amount, 2),
            picks=[],
            passed=passed,
            invested=0.0,
            cash_remaining=round(amount, 2),
            considered=considered,
            source=source,
            note=note,
        )

    # --- accepting (writes PROPOSED rows — never APPROVED) ---
    async def accept(self, user_id: uuid.UUID, body: DeployAcceptIn) -> ProposalOut:
        # A new batch supersedes anything still open, same as the rebalancer —
        # otherwise a stale checklist and a fresh one both look actionable.
        await self.proposals.expire_open_proposals(user_id)
        batch_id = uuid.uuid4()

        orders = [
            ProposedOrder(
                user_id=user_id,
                batch_id=batch_id,
                kind="deploy",
                symbol=o.symbol.strip().upper(),
                side=OrderSide.BUY,
                order_type="market",
                qty=float(o.qty),
                notional_cents=None,
                est_value_cents=round(o.qty * o.price * 100),
                reason=(
                    o.reason.strip()
                    or f"Buy {o.qty} share(s) of {o.symbol.upper()} "
                    f"≈ ${o.qty * o.price:,.2f} CAD from your cash plan."
                ),
            )
            for o in body.orders
        ]
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
