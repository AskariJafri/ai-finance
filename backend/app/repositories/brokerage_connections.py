from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrokerageConnection
from app.domain.enums import BrokerageConnectionStatus


class BrokerageConnectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[BrokerageConnection]:
        result = await self.session.execute(
            select(BrokerageConnection)
            .where(BrokerageConnection.user_id == user_id)
            .order_by(BrokerageConnection.created_at)
        )
        return list(result.scalars().all())

    async def get(self, user_id: uuid.UUID, connection_id: uuid.UUID) -> BrokerageConnection | None:
        result = await self.session.execute(
            select(BrokerageConnection).where(
                BrokerageConnection.user_id == user_id,
                BrokerageConnection.id == connection_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_account(
        self, user_id: uuid.UUID, brokerage_account_id: str
    ) -> BrokerageConnection | None:
        result = await self.session.execute(
            select(BrokerageConnection).where(
                BrokerageConnection.user_id == user_id,
                BrokerageConnection.brokerage_account_id == brokerage_account_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        brokerage_account_id: str,
        account_name: str | None,
        institution_name: str | None,
        number_masked: str | None,
        currency: str,
        cash_cents: int,
        total_value_cents: int,
        trading_enabled: bool,
    ) -> BrokerageConnection:
        row = await self.get_by_account(user_id, brokerage_account_id)
        if row is None:
            row = BrokerageConnection(user_id=user_id, brokerage_account_id=brokerage_account_id)
            self.session.add(row)
        row.account_name = account_name
        row.institution_name = institution_name
        row.number_masked = number_masked
        row.currency = currency
        row.cash_cents = cash_cents
        row.total_value_cents = total_value_cents
        row.trading_enabled = trading_enabled
        row.status = BrokerageConnectionStatus.ACTIVE
        row.last_synced_at = datetime.now(UTC)
        await self.session.flush()
        return row

    async def mark_status(
        self, row: BrokerageConnection, status: BrokerageConnectionStatus
    ) -> None:
        row.status = status
        await self.session.flush()

    async def delete(self, row: BrokerageConnection) -> None:
        """Remove one connection — an account closed or de-authorized at the
        broker. Unlinking everything is a separate, user-initiated action."""
        await self.session.delete(row)
        await self.session.flush()

    async def delete_all_for_user(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            delete(BrokerageConnection).where(BrokerageConnection.user_id == user_id)
        )
        await self.session.flush()
        return int(result.rowcount or 0)
