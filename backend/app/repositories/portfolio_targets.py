from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PortfolioTarget


class PortfolioTargetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[PortfolioTarget]:
        result = await self.session.execute(
            select(PortfolioTarget)
            .where(PortfolioTarget.user_id == user_id)
            .order_by(PortfolioTarget.symbol)
        )
        return list(result.scalars().all())

    async def replace_all(
        self, user_id: uuid.UUID, targets: list[tuple[str, float]]
    ) -> list[PortfolioTarget]:
        await self.delete_all_for_user(user_id)
        rows = [
            PortfolioTarget(user_id=user_id, symbol=symbol.upper(), weight=weight)
            for symbol, weight in targets
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def delete_all_for_user(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            delete(PortfolioTarget).where(PortfolioTarget.user_id == user_id)
        )
        await self.session.flush()
        return result.rowcount or 0
