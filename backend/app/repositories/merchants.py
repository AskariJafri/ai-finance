from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Merchant


class MerchantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_normalized(self, normalized_name: str) -> Merchant | None:
        result = await self.session.execute(
            select(Merchant).where(Merchant.normalized_name == normalized_name)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, *, normalized_name: str, display_name: str) -> Merchant:
        merchant = await self.get_by_normalized(normalized_name)
        if merchant is None:
            merchant = Merchant(
                normalized_name=normalized_name, display_name=display_name or normalized_name
            )
            self.session.add(merchant)
            await self.session.flush()
        return merchant
