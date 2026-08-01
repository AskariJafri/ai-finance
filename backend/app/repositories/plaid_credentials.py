from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlaidCredential


class PlaidCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: uuid.UUID) -> PlaidCredential | None:
        result = await self.session.execute(
            select(PlaidCredential).where(PlaidCredential.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        client_id: str,
        secret_encrypted: str,
        plaid_env: str,
    ) -> PlaidCredential:
        cred = await self.get_for_user(user_id)
        if cred is None:
            cred = PlaidCredential(user_id=user_id)
            self.session.add(cred)
        cred.client_id = client_id
        cred.secret_encrypted = secret_encrypted
        cred.plaid_env = plaid_env
        await self.session.flush()
        return cred

    async def delete_for_user(self, user_id: uuid.UUID) -> bool:
        cred = await self.get_for_user(user_id)
        if cred is None:
            return False
        await self.session.delete(cred)
        await self.session.flush()
        return True
