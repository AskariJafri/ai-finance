from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, PlaidItem, Transaction
from app.domain.enums import PlaidItemStatus


class PlaidItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, item_id: uuid.UUID) -> PlaidItem | None:
        return await self.session.get(PlaidItem, item_id)

    async def get_by_plaid_item_id(self, plaid_item_id: str) -> PlaidItem | None:
        result = await self.session.execute(
            select(PlaidItem).where(PlaidItem.plaid_item_id == plaid_item_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[PlaidItem]:
        result = await self.session.execute(select(PlaidItem).where(PlaidItem.user_id == user_id))
        return list(result.scalars().all())

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        plaid_item_id: str,
        access_token_encrypted: str,
        institution_id: str | None,
        institution_name: str | None,
    ) -> PlaidItem:
        item = PlaidItem(
            user_id=user_id,
            plaid_item_id=plaid_item_id,
            access_token_encrypted=access_token_encrypted,
            institution_id=institution_id,
            institution_name=institution_name,
            status=PlaidItemStatus.ACTIVE,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def delete_all_for_user(self, user_id: uuid.UUID) -> int:
        """Delete ALL of a user's items, with their accounts and transactions.
        Single-connection policy: linking a bank replaces whatever was linked
        before. Explicit child deletes rather than FK cascade — SQLite (tests)
        doesn't enforce ON DELETE CASCADE by default. Returns items deleted."""
        item_ids = select(PlaidItem.id).where(PlaidItem.user_id == user_id)
        account_ids = select(Account.id).where(Account.plaid_item_id.in_(item_ids))
        await self.session.execute(
            delete(Transaction).where(Transaction.account_id.in_(account_ids))
        )
        await self.session.execute(delete(Account).where(Account.plaid_item_id.in_(item_ids)))
        result = await self.session.execute(delete(PlaidItem).where(PlaidItem.user_id == user_id))
        await self.session.flush()
        return result.rowcount or 0

    async def set_cursor(self, item: PlaidItem, cursor: str | None) -> None:
        item.transactions_cursor = cursor
        await self.session.flush()
