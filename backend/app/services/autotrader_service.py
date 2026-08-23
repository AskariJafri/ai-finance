"""Auto-Trader — a disciplined, rules-based scanner over the real portfolio.

HONEST: this does not beat the market. Its edge is discipline: pre-committed
signals, capped sizing and a max-position guardrail, applied the same way every
run whether or not the numbers look exciting.

**It suggests; it never trades.** A run reads the real holdings and writes
PROPOSED orders into the same trade checklist the rebalancer and the AI planner
use — nothing here can set APPROVED, which is the rule the whole invest module
is built on. `dry_run` decides whether a run writes to that checklist at all or
just reports what it saw; either way the user approves each order by hand, at
their own broker or through the brokerage link.

The signal itself (`signal_for`) is unchanged and still shared with the cash
planner, so the rules that rank a candidate are the rules that judge a holding.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.indicators import Indicators, compute_indicators
from app.db.models import AutoTraderConfig, AutoTradeRun, ProposedOrder
from app.domain.enums import OrderSide
from app.integrations.marketdata.provider import MarketDataProvider
from app.repositories.holdings import HoldingRepository
from app.repositories.paper import PaperRepository
from app.repositories.proposed_orders import ProposedOrderRepository
from app.repositories.user_settings import UserSettingsRepository
from app.schemas.paper import AutoTraderConfigIn, AutoTraderConfigOut, AutoTradeRunOut
from app.services.invest_service import InvestService
from app.services.market_data_service import MarketDataService

DEFAULT_UNIVERSE = ["SPY", "AAPL", "MSFT", "NVDA", "VFV.TO"]

# Below this a suggestion is not worth the user's attention or the spread.
MIN_PROPOSAL_CAD = 20.0


def signal_for(ind: Indicators, held: bool) -> tuple[float, str, str]:
    """Return (score, action, reason). action ∈ buy|sell|hold|skip.

    Module-level and pure so the cash-deploy planner can rank a candidate pool
    with the same pre-committed rules the auto-trader trades on — no LLM calls,
    so it costs nothing to run over the whole universe.
    """
    score = 0.0
    if ind.trend == "up":
        score += 1
    elif ind.trend == "down":
        score -= 1
    if ind.rsi14 is not None:
        if ind.rsi14 >= 75:
            score -= 1
        elif ind.rsi14 <= 30:
            score += 1
    if (ind.ret_3m or 0) > 0:
        score += 0.5

    if not held and ind.trend == "up" and (ind.rsi14 is None or ind.rsi14 < 70) and score >= 1.5:
        return score, "buy", f"uptrend, RSI {ind.rsi14}, 3m {(ind.ret_3m or 0) * 100:+.0f}%"
    if held and (ind.trend == "down" or (ind.rsi14 or 0) >= 80):
        return score, "sell", f"exit signal (trend {ind.trend}, RSI {ind.rsi14})"
    return score, ("hold" if held else "skip"), f"no edge (trend {ind.trend}, RSI {ind.rsi14})"


class AutoTraderService:
    def __init__(self, session: AsyncSession, provider: MarketDataProvider) -> None:
        self.session = session
        # Config and run history still live in the paper tables — they are the
        # bot's own records, not simulated money.
        self.repo = PaperRepository(session)
        self.market_data = MarketDataService(session, provider)
        self.proposals = ProposedOrderRepository(session)
        self.holdings = HoldingRepository(session)
        self.user_settings = UserSettingsRepository(session)
        self.invest = InvestService(session, provider)

    async def get_config(self, user_id: uuid.UUID) -> AutoTraderConfigOut:
        cfg = await self.repo.get_or_create_config(user_id)
        await self.session.commit()
        return self._cfg_out(cfg)

    async def update_config(
        self, user_id: uuid.UUID, body: AutoTraderConfigIn
    ) -> AutoTraderConfigOut:
        cfg = await self.repo.get_or_create_config(user_id)
        if body.enabled is not None:
            cfg.enabled = body.enabled
        if body.dry_run is not None:
            cfg.dry_run = body.dry_run
        if body.max_positions is not None:
            cfg.max_positions = body.max_positions
        if body.per_trade is not None:
            cfg.per_trade_cents = round(body.per_trade * 100)
        if body.universe is not None:
            cfg.universe = ",".join(s.strip().upper() for s in body.universe if s.strip())
        await self.session.commit()
        return self._cfg_out(cfg)

    _signal = staticmethod(signal_for)

    async def run(self, user_id: uuid.UUID) -> AutoTradeRunOut:
        """Scan the universe against the real portfolio and propose.

        The universe is whatever the user configured plus everything they
        already hold: a scanner that only looks at a fixed list can tell you
        what to buy but never notices that something you own has turned.
        """
        cfg = await self.repo.get_or_create_config(user_id)
        configured = [s for s in cfg.universe.split(",") if s] or DEFAULT_UNIVERSE

        # Universe first, from the config plus whatever is actually held, then
        # price it — `portfolio()` values positions from cached bars, so asking
        # it before the sync reports an empty book and the bot decides nothing.
        rows = await self.holdings.list_for_user(user_id)
        universe: list[str] = []
        for symbol in [*configured, *(h.symbol for h in rows)]:
            if symbol.upper() not in universe:
                universe.append(symbol.upper())

        await self.market_data.sync_bars(universe)
        await self.session.commit()

        portfolio = await self.invest.portfolio(user_id)
        positions = {p["symbol"].upper(): p for p in portfolio.positions}
        held_count = len(positions)
        # Read cash from settings rather than the portfolio: with no holdings
        # yet, `portfolio()` reports "not connected" and no account at all —
        # but the cash is real and the bot should be able to deploy it.
        settings_row = await self.user_settings.get_or_create(user_id)
        cash = settings_row.invest_cash_cents / 100

        # `enabled` arms the bot; `dry_run` decides whether an armed run writes
        # to the checklist or only reports. Nothing here ever executes.
        propose = cfg.enabled and not cfg.dry_run

        decisions: list[dict] = []
        orders: list[ProposedOrder] = []
        batch_id = uuid.uuid4()
        budget = cash

        series = await self.market_data.get_close_series(universe)

        for sym in universe:
            closes = [c for _, c in series.get(sym, [])]
            try:
                ind = compute_indicators(closes)
            except ValueError:
                decisions.append(
                    {
                        "symbol": sym,
                        "action": "skip",
                        "reason": "no price history",
                        "score": None,
                        "executed": False,
                    }
                )
                continue

            position = positions.get(sym)
            held = position is not None
            score, action, reason = self._signal(ind, held)

            # Guardrail: never exceed max open positions on new buys.
            if action == "buy" and not held and held_count >= cfg.max_positions:
                action, reason = "skip", f"at max positions ({cfg.max_positions})"

            amount = 0.0
            if action == "buy":
                amount = min(cfg.per_trade_cents / 100, budget)
                if amount < MIN_PROPOSAL_CAD:
                    action, reason = "skip", "not enough cash for a trade worth placing"
            elif action == "sell" and position:
                # Exit the whole position. The signal is an exit signal, not a
                # sizing opinion, so inventing a partial would be making up a
                # decision the rules never took.
                amount = position["market_value"]
                if amount < MIN_PROPOSAL_CAD:
                    action, reason = "hold", "position too small to be worth selling"

            proposed = False
            if propose and action in ("buy", "sell") and amount >= MIN_PROPOSAL_CAD:
                orders.append(
                    self._order(
                        user_id=user_id,
                        batch_id=batch_id,
                        symbol=sym,
                        side=OrderSide.BUY if action == "buy" else OrderSide.SELL,
                        amount=amount,
                        reason=f"Auto-trader: {reason}.",
                    )
                )
                proposed = True
                if action == "buy":
                    budget -= amount
                    held_count += 1

            decisions.append(
                {
                    "symbol": sym,
                    "action": action,
                    "reason": reason,
                    "score": round(score, 2),
                    # Kept as `executed` for the stored shape, but nothing is
                    # executed any more — it means "written to the checklist".
                    "executed": proposed,
                }
            )

        if orders:
            # A new batch supersedes anything still open, same as the rebalancer
            # — two live checklists both looking actionable is worse than one.
            await self.proposals.expire_open_proposals(user_id)
            await self.proposals.add_all(orders)

        mode = (
            "proposed to your checklist"
            if propose
            else ("report only (dry run)" if cfg.enabled else "preview (bot disabled)")
        )
        summary = (
            f"Scanned {len(universe)} tickers · {len(orders)} suggestion(s) · {mode}. "
            "Nothing is placed until you approve each order."
        )
        run = await self.repo.add_run(
            user_id=user_id,
            dry_run=not propose,
            scanned=len(universe),
            executed=len(orders),
            summary=summary,
            decisions=decisions,
        )
        await self.session.commit()
        return self._run_out(run)

    def _order(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
        symbol: str,
        side: OrderSide,
        amount: float,
        reason: str,
    ) -> ProposedOrder:
        """A dollar-sized PROPOSED order. Never APPROVED, never routed."""
        return ProposedOrder(
            user_id=user_id,
            batch_id=batch_id,
            kind="autotrader",
            symbol=symbol,
            side=side,
            order_type="market",
            qty=None,
            notional_cents=round(amount * 100),
            est_value_cents=round(amount * 100),
            reason=reason,
        )

    async def runs(self, user_id: uuid.UUID) -> list[AutoTradeRunOut]:
        return [self._run_out(r) for r in await self.repo.list_runs(user_id)]

    @staticmethod
    def _cfg_out(cfg: AutoTraderConfig) -> AutoTraderConfigOut:
        return AutoTraderConfigOut(
            enabled=cfg.enabled,
            dry_run=cfg.dry_run,
            max_positions=cfg.max_positions,
            per_trade=round(cfg.per_trade_cents / 100, 2),
            universe=[s for s in cfg.universe.split(",") if s] or DEFAULT_UNIVERSE,
        )

    @staticmethod
    def _run_out(run: AutoTradeRun) -> AutoTradeRunOut:
        return AutoTradeRunOut(
            id=str(run.id),
            dry_run=run.dry_run,
            scanned=run.scanned,
            executed=run.executed,
            summary=run.summary,
            decisions=(run.decisions or {}).get("items", []),
            created_at=run.created_at.isoformat(),
        )
