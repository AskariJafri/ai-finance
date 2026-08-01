from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProposedOrder
from app.domain.enums import ProposedOrderStatus


class ProposedOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: uuid.UUID, order_id: uuid.UUID) -> ProposedOrder | None:
        result = await self.session.execute(
            select(ProposedOrder).where(
                ProposedOrder.id == order_id, ProposedOrder.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        status: ProposedOrderStatus | None = None,
        limit: int = 100,
    ) -> list[ProposedOrder]:
        stmt = select(ProposedOrder).where(ProposedOrder.user_id == user_id)
        if status is not None:
            stmt = stmt.where(ProposedOrder.status == status)
        result = await self.session.execute(
            stmt.order_by(ProposedOrder.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def add_all(self, orders: list[ProposedOrder]) -> list[ProposedOrder]:
        self.session.add_all(orders)
        await self.session.flush()
        return orders

    async def expire_open_proposals(self, user_id: uuid.UUID) -> int:
        """A new proposal batch supersedes any orders still awaiting a decision."""
        open_orders = await self.list_for_user(user_id, ProposedOrderStatus.PROPOSED)
        for order in open_orders:
            order.status = ProposedOrderStatus.EXPIRED
        await self.session.flush()
        return len(open_orders)

    async def delete_all_for_user(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            delete(ProposedOrder).where(ProposedOrder.user_id == user_id)
        )
        await self.session.flush()
        return result.rowcount or 0
