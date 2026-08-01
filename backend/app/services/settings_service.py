"""User settings: manage per-user Plaid credentials.

Secrets are Fernet-encrypted before storage. The status endpoint reports where
the effective Plaid config comes from (the user's own keys, the server
environment, or the built-in mock) so the app can show the right state.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import TokenCipher
from app.repositories.plaid_credentials import PlaidCredentialRepository
from app.repositories.user_settings import UserSettingsRepository
from app.schemas.common import cents_to_dollars
from app.schemas.settings import BudgetSettings, PlaidCredentialStatus


def _mask(client_id: str) -> str:
    if len(client_id) <= 4:
        return "•" * len(client_id)
    return f"{'•' * (len(client_id) - 4)}{client_id[-4:]}"


class SettingsService:
    def __init__(self, session: AsyncSession, cipher: TokenCipher) -> None:
        self.session = session
        self.cipher = cipher
        self.creds = PlaidCredentialRepository(session)
        self.user_settings = UserSettingsRepository(session)

    async def get_plaid_status(self, user_id: uuid.UUID) -> PlaidCredentialStatus:
        cred = await self.creds.get_for_user(user_id)
        if cred is not None:
            return PlaidCredentialStatus(
                configured=True,
                source="user",
                plaid_env=cred.plaid_env,
                client_id_masked=_mask(cred.client_id),
            )
        if not settings.use_mock_plaid:
            return PlaidCredentialStatus(
                configured=True,
                source="environment",
                plaid_env=settings.plaid_env,
                client_id_masked=_mask(settings.plaid_client_id),
            )
        return PlaidCredentialStatus(
            configured=False, source="mock", plaid_env="sandbox", client_id_masked=None
        )

    async def set_plaid(
        self, user_id: uuid.UUID, *, client_id: str, secret: str, plaid_env: str
    ) -> PlaidCredentialStatus:
        await self.creds.upsert(
            user_id=user_id,
            client_id=client_id,
            secret_encrypted=self.cipher.encrypt(secret),
            plaid_env=plaid_env,
        )
        await self.session.commit()
        return await self.get_plaid_status(user_id)

    async def delete_plaid(self, user_id: uuid.UUID) -> PlaidCredentialStatus:
        await self.creds.delete_for_user(user_id)
        await self.session.commit()
        return await self.get_plaid_status(user_id)

    async def get_income_override_cents(self, user_id: uuid.UUID) -> int | None:
        row = await self.user_settings.get_for_user(user_id)
        return row.monthly_income_override_cents if row is not None else None

    async def get_budget(self, user_id: uuid.UUID) -> BudgetSettings:
        cents = await self.get_income_override_cents(user_id)
        return BudgetSettings(
            monthly_income_override=cents_to_dollars(cents) if cents is not None else None
        )

    async def set_budget(self, user_id: uuid.UUID, body: BudgetSettings) -> BudgetSettings:
        cents = (
            round(body.monthly_income_override * 100)
            if body.monthly_income_override is not None
            else None
        )
        await self.user_settings.set_income_override(user_id, cents)
        await self.session.commit()
        return await self.get_budget(user_id)
