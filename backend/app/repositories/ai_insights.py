from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AIInsight


class AIInsightRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID, kind: str, period: str) -> AIInsight | None:
        result = await self.session.execute(
            select(AIInsight).where(
                AIInsight.user_id == user_id,
                AIInsight.kind == kind,
                AIInsight.period == period,
            )
        )
        return result.scalar_one_or_none()

    async def delete(self, user_id: uuid.UUID, kind: str, period: str) -> bool:
        """Drop a cached insight so the next read regenerates it.

        Used when the figures underneath an insight change mid-period — a
        brokerage sync, say — and serving the cached text would describe a
        portfolio the user no longer has.
        """
        row = await self.get(user_id, kind, period)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        kind: str,
        period: str,
        content: str,
        model: str | None,
    ) -> AIInsight:
        row = await self.get(user_id, kind, period)
        if row is None:
            row = AIInsight(user_id=user_id, kind=kind, period=period)
            self.session.add(row)
        row.content = content
        row.model = model
        await self.session.flush()
        return row
