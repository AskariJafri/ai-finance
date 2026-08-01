from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AutoTraderConfig,
    AutoTradeRun,
    PaperAccount,
    PaperPosition,
    PaperTrade,
)

STARTING_CASH_CENTS = 10_000_000  # $100,000 CAD paper money


class PaperRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- account ---
    async def get_or_create_account(self, user_id: uuid.UUID) -> PaperAccount:
        result = await self.session.execute(
            select(PaperAccount).where(PaperAccount.user_id == user_id)
        )
        acct = result.scalar_one_or_none()
        if acct is None:
            acct = PaperAccount(user_id=user_id, cash_cents=STARTING_CASH_CENTS)
            self.session.add(acct)
            await self.session.flush()
        return acct

    # --- positions ---
    async def list_positions(self, user_id: uuid.UUID) -> list[PaperPosition]:
        result = await self.session.execute(
            select(PaperPosition)
            .where(PaperPosition.user_id == user_id)
            .order_by(PaperPosition.symbol.asc())
        )
        return list(result.scalars().all())

    async def get_position(self, user_id: uuid.UUID, symbol: str) -> PaperPosition | None:
        result = await self.session.execute(
            select(PaperPosition).where(
                PaperPosition.user_id == user_id, PaperPosition.symbol == symbol
            )
        )
        return result.scalar_one_or_none()

    async def add_position(
        self, user_id: uuid.UUID, symbol: str, qty: float, avg_price_cents: int, opened_by: str
    ) -> PaperPosition:
        pos = PaperPosition(
            user_id=user_id,
            symbol=symbol,
            qty=qty,
            avg_price_cents=avg_price_cents,
            opened_by=opened_by,
        )
        self.session.add(pos)
        await self.session.flush()
        return pos

    async def delete_position(self, pos: PaperPosition) -> None:
        await self.session.delete(pos)
        await self.session.flush()

    # --- trades ---
    async def add_trade(
        self,
        *,
        user_id: uuid.UUID,
        symbol: str,
        side: str,
        qty: float,
        price_cents: int,
        source: str,
        note: str | None = None,
    ) -> PaperTrade:
        trade = PaperTrade(
            user_id=user_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price_cents=price_cents,
            source=source,
            note=note,
        )
        self.session.add(trade)
        await self.session.flush()
        return trade

    async def list_trades(self, user_id: uuid.UUID, limit: int = 50) -> list[PaperTrade]:
        result = await self.session.execute(
            select(PaperTrade)
            .where(PaperTrade.user_id == user_id)
            .order_by(PaperTrade.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # --- autotrader config + runs ---
    async def get_or_create_config(self, user_id: uuid.UUID) -> AutoTraderConfig:
        result = await self.session.execute(
            select(AutoTraderConfig).where(AutoTraderConfig.user_id == user_id)
        )
        cfg = result.scalar_one_or_none()
        if cfg is None:
            cfg = AutoTraderConfig(user_id=user_id)
            self.session.add(cfg)
            await self.session.flush()
        return cfg

    async def add_run(
        self,
        *,
        user_id: uuid.UUID,
        dry_run: bool,
        scanned: int,
        executed: int,
        summary: str,
        decisions: list[dict],
    ) -> AutoTradeRun:
        run = AutoTradeRun(
            user_id=user_id,
            dry_run=dry_run,
            scanned=scanned,
            executed=executed,
            summary=summary,
            decisions={"items": decisions},
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def list_runs(self, user_id: uuid.UUID, limit: int = 20) -> list[AutoTradeRun]:
        result = await self.session.execute(
            select(AutoTradeRun)
            .where(AutoTradeRun.user_id == user_id)
            .order_by(AutoTradeRun.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
