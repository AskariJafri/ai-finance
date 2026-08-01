from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserSettings


class UserSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: uuid.UUID) -> UserSettings | None:
        result = await self.session.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, user_id: uuid.UUID) -> UserSettings:
        row = await self.get_for_user(user_id)
        if row is None:
            row = UserSettings(user_id=user_id)
            self.session.add(row)
            await self.session.flush()
        return row

    async def set_income_override(self, user_id: uuid.UUID, cents: int | None) -> UserSettings:
        row = await self.get_or_create(user_id)
        row.monthly_income_override_cents = cents
        await self.session.flush()
        return row

    async def set_snaptrade_registration(
        self, user_id: uuid.UUID, *, snaptrade_user_id: str, secret_encrypted: str
    ) -> UserSettings:
        row = await self.get_or_create(user_id)
        row.snaptrade_user_id = snaptrade_user_id
        row.snaptrade_secret_encrypted = secret_encrypted
        await self.session.flush()
        return row

    async def clear_snaptrade_registration(self, user_id: uuid.UUID) -> None:
        row = await self.get_for_user(user_id)
        if row is None:
            return
        row.snaptrade_user_id = None
        row.snaptrade_secret_encrypted = None
        await self.session.flush()
