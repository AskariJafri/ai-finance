from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WatchlistItem


class WatchlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[WatchlistItem]:
        result = await self.session.execute(
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.symbol.asc())
        )
        return list(result.scalars().all())

    async def get(self, user_id: uuid.UUID, symbol: str) -> WatchlistItem | None:
        result = await self.session.execute(
            select(WatchlistItem).where(
                WatchlistItem.user_id == user_id, WatchlistItem.symbol == symbol
            )
        )
        return result.scalar_one_or_none()

    async def add(self, user_id: uuid.UUID, symbol: str, name: str | None) -> WatchlistItem:
        item = WatchlistItem(user_id=user_id, symbol=symbol, name=name)
        self.session.add(item)
        await self.session.flush()
        return item

    async def delete(self, user_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        item = await self.session.get(WatchlistItem, item_id)
        if item is None or item.user_id != user_id:
            return False
        await self.session.delete(item)
        await self.session.flush()
        return True
