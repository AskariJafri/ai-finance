from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalysisSession


class AnalysisSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        user_id: uuid.UUID,
        symbol: str,
        action: str,
        confidence: float,
        summary: str,
        source: str,
        payload: dict,
    ) -> AnalysisSession:
        row = AnalysisSession(
            user_id=user_id,
            symbol=symbol,
            action=action,
            confidence=confidence,
            summary=summary,
            source=source,
            payload=payload,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_user(self, user_id: uuid.UUID, limit: int = 25) -> list[AnalysisSession]:
        result = await self.session.execute(
            select(AnalysisSession)
            .where(AnalysisSession.user_id == user_id)
            .order_by(AnalysisSession.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get(self, user_id: uuid.UUID, session_id: uuid.UUID) -> AnalysisSession | None:
        row = await self.session.get(AnalysisSession, session_id)
        if row is None or row.user_id != user_id:
            return None
        return row
