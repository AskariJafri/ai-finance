from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Holding


class HoldingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[Holding]:
        result = await self.session.execute(
            select(Holding).where(Holding.user_id == user_id).order_by(Holding.symbol)
        )
        return list(result.scalars().all())

    async def get(self, user_id: uuid.UUID, symbol: str) -> Holding | None:
        result = await self.session.execute(
            select(Holding).where(Holding.user_id == user_id, Holding.symbol == symbol.upper())
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID, holding_id: uuid.UUID) -> Holding | None:
        result = await self.session.execute(
            select(Holding).where(Holding.id == holding_id, Holding.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        symbol: str,
        qty: float,
        avg_cost_cents: int,
        currency: str,
        name: str | None,
        is_synced: bool = False,
    ) -> Holding:
        row = await self.get(user_id, symbol)
        if row is None:
            row = Holding(user_id=user_id, symbol=symbol.upper())
            self.session.add(row)
        row.qty = qty
        row.avg_cost_cents = avg_cost_cents
        row.currency = currency
        row.name = name
        row.is_synced = is_synced
        await self.session.flush()
        return row

    async def list_synced(self, user_id: uuid.UUID) -> list[Holding]:
        """Broker-mirrored rows only — the set a sync is allowed to prune."""
        result = await self.session.execute(
            select(Holding).where(Holding.user_id == user_id, Holding.is_synced.is_(True))
        )
        return list(result.scalars().all())

    async def delete(self, user_id: uuid.UUID, holding_id: uuid.UUID) -> bool:
        row = await self.get_by_id(user_id, holding_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def delete_all_for_user(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(delete(Holding).where(Holding.user_id == user_id))
        await self.session.flush()
        return result.rowcount or 0
