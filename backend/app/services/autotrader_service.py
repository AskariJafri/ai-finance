"""Autonomous paper Auto-Trader — a disciplined, rules-based scanner.

HONEST: this does not beat the market. Its edge is discipline: pre-committed
signals, capped sizing, a max-position guardrail, and it only ever sells
positions it opened itself. SAFE BY DEFAULT — disabled + dry-run, so it does
nothing until the user opts in. Operates only on the paper sandbox.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.indicators import Indicators, compute_indicators
from app.db.models import AutoTraderConfig, AutoTradeRun
from app.integrations.marketdata.provider import MarketDataProvider
from app.repositories.paper import PaperRepository
from app.schemas.paper import AutoTraderConfigIn, AutoTraderConfigOut, AutoTradeRunOut
from app.services.errors import ValidationFailed
from app.services.market_data_service import MarketDataService
from app.services.paper_broker_service import PaperBrokerService

DEFAULT_UNIVERSE = ["SPY", "AAPL", "MSFT", "NVDA", "VFV.TO"]


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
        self.repo = PaperRepository(session)
        self.market_data = MarketDataService(session, provider)
        self.broker = PaperBrokerService(session, provider)

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
        cfg = await self.repo.get_or_create_config(user_id)
        universe = [s for s in cfg.universe.split(",") if s] or DEFAULT_UNIVERSE
        live = cfg.enabled and not cfg.dry_run
        positions = {p.symbol: p for p in await self.repo.list_positions(user_id)}
        held_count = len(positions)

        decisions: list[dict] = []
        executed = 0

        for sym in universe:
            await self.market_data.sync_bars([sym])
            closes = [c for _, c in (await self.market_data.get_close_series([sym])).get(sym, [])]
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

            held = sym in positions
            score, action, reason = self._signal(ind, held)

            # Guardrail: never exceed max open positions on new buys.
            if action == "buy" and not held and held_count >= cfg.max_positions:
                action, reason = "skip", f"at max positions ({cfg.max_positions})"
            # Only ever sell positions the bot opened itself.
            if action == "sell" and (not held or positions[sym].opened_by != "autotrader"):
                action, reason = "hold", "not an auto-trader position — left untouched"

            done = False
            if live and action == "buy":
                price = await self.broker.price_cad(sym)
                qty = int((cfg.per_trade_cents / 100) / price) if price else 0
                if qty >= 1:
                    try:
                        await self.broker.buy(user_id, sym, qty, source="autotrader", note=reason)
                        done, executed, held_count = True, executed + 1, held_count + 1
                    except ValidationFailed as exc:
                        action, reason = "skip", str(exc)
                else:
                    action, reason = "skip", "position size below one share"
            elif live and action == "sell":
                try:
                    await self.broker.sell(
                        user_id, sym, positions[sym].qty, source="autotrader", note=reason
                    )
                    done, executed = True, executed + 1
                except ValidationFailed as exc:
                    action, reason = "hold", str(exc)

            decisions.append(
                {
                    "symbol": sym,
                    "action": action,
                    "reason": reason,
                    "score": round(score, 2),
                    "executed": done,
                }
            )

        mode = "executed live" if live else ("dry-run" if cfg.enabled else "preview (disabled)")
        summary = f"Scanned {len(universe)} tickers · {executed} trade(s) · {mode}."
        run = await self.repo.add_run(
            user_id=user_id,
            dry_run=not live,
            scanned=len(universe),
            executed=executed,
            summary=summary,
            decisions=decisions,
        )
        await self.session.commit()
        return self._run_out(run)

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
