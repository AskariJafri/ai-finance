from __future__ import annotations

from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PriceBar


class PriceBarRepository:
    """Global (cross-user) daily close cache."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def latest_date(self, symbol: str) -> date_type | None:
        result = await self.session.execute(
            select(PriceBar.date)
            .where(PriceBar.symbol == symbol)
            .order_by(PriceBar.date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def earliest_date(self, symbol: str) -> date_type | None:
        result = await self.session.execute(
            select(PriceBar.date)
            .where(PriceBar.symbol == symbol)
            .order_by(PriceBar.date.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_series(
        self,
        symbol: str,
        start: date_type | None = None,
        end: date_type | None = None,
    ) -> list[tuple[date_type, float]]:
        stmt = select(PriceBar.date, PriceBar.close).where(PriceBar.symbol == symbol)
        if start is not None:
            stmt = stmt.where(PriceBar.date >= start)
        if end is not None:
            stmt = stmt.where(PriceBar.date <= end)
        rows = (await self.session.execute(stmt.order_by(PriceBar.date))).all()
        return [(d, float(c)) for d, c in rows]

    async def bulk_upsert(self, symbol: str, points: list[tuple[date_type, float]]) -> int:
        """Insert new (date, close) points; skip dates already cached."""
        if not points:
            return 0
        existing = set(
            (
                await self.session.execute(
                    select(PriceBar.date).where(
                        PriceBar.symbol == symbol,
                        PriceBar.date >= min(d for d, _ in points),
                    )
                )
            )
            .scalars()
            .all()
        )
        added = 0
        for d, close in points:
            if d in existing:
                continue
            self.session.add(PriceBar(symbol=symbol, date=d, close=close))
            added += 1
        await self.session.flush()
        return added
