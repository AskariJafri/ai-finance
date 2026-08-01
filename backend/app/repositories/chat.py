from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, ChatSession


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- sessions ---

    async def create_session(self, user_id: uuid.UUID, title: str) -> ChatSession:
        row = ChatSession(user_id=user_id, title=title[:120])
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_session(self, user_id: uuid.UUID, session_id: uuid.UUID) -> ChatSession | None:
        result = await self.session.execute(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_sessions(
        self, user_id: uuid.UUID, limit: int = 50
    ) -> list[tuple[ChatSession, int]]:
        """Sessions newest-activity-first, each with its message count."""
        count = (
            select(ChatMessage.session_id, func.count().label("n"))
            .group_by(ChatMessage.session_id)
            .subquery()
        )
        result = await self.session.execute(
            select(ChatSession, func.coalesce(count.c.n, 0))
            .outerjoin(count, count.c.session_id == ChatSession.id)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def delete_session(self, user_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
        )
        return bool(result.rowcount)

    # --- messages ---

    async def add_message(
        self,
        *,
        session_id: uuid.UUID,
        role: str,
        content: str,
        source: str | None = None,
        model: str | None = None,
        context_used: list[str] | None = None,
    ) -> ChatMessage:
        row = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            source=source,
            model=model,
            context_used=context_used,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_messages(self, session_id: uuid.UUID, limit: int = 200) -> list[ChatMessage]:
        result = await self.session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def recent_turns(self, session_id: uuid.UUID, limit: int = 8) -> list[ChatMessage]:
        """The last ``limit`` messages, oldest-first.

        Bounded because the whole thread is re-sent as prompt context on every
        turn and a local model's context window is small.
        """
        result = await self.session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))
