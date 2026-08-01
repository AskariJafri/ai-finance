from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_plaid_account_id(self, plaid_account_id: str) -> Account | None:
        result = await self.session.execute(
            select(Account).where(Account.plaid_account_id == plaid_account_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[Account]:
        result = await self.session.execute(
            select(Account).where(Account.user_id == user_id).order_by(Account.name)
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        plaid_item_id: uuid.UUID,
        plaid_account_id: str,
        name: str,
        official_name: str | None,
        mask: str | None,
        type: str | None,
        subtype: str | None,
        currency: str,
        current_balance_cents: int,
        available_balance_cents: int | None,
    ) -> Account:
        account = await self.get_by_plaid_account_id(plaid_account_id)
        if account is None:
            account = Account(user_id=user_id, plaid_account_id=plaid_account_id)
            self.session.add(account)
        account.plaid_item_id = plaid_item_id
        account.name = name
        account.official_name = official_name
        account.mask = mask
        account.type = type
        account.subtype = subtype
        account.currency = currency
        account.current_balance_cents = current_balance_cents
        account.available_balance_cents = available_balance_cents
        await self.session.flush()
        return account
