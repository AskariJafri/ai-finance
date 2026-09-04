from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProposedOrder
from app.domain.enums import ProposedOrderStatus


class ProposedOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(
        self, user_id: uuid.UUID, order_id: uuid.UUID, *, for_update: bool = False
    ) -> ProposedOrder | None:
        """One order, scoped to its owner.

        ``for_update`` takes a row lock for the rest of the transaction, and
        every caller that goes on to *act* on the order must use it. Reading the
        status and then acting on it is a check followed by a use, and without
        the lock two requests can pass the same check: a double-tapped Approve
        button, or a retry after a slow response, places the trade twice. The
        lock makes the second request wait and then find the status it expects
        to be exclusive already changed.

        SQLite has no row locks and ignores the clause; the tests run there and
        the deployment runs Postgres, which is where it matters.
        """
        stmt = select(ProposedOrder).where(
            ProposedOrder.id == order_id, ProposedOrder.user_id == user_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
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
