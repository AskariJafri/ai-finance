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

    async def recent_closes(self, symbol: str, limit: int = 2) -> list[tuple[date_type, float]]:
        """The last ``limit`` cached sessions, oldest first.

        Day-over-day change needs the two most recent bars, and pulling the
        whole history back just to read its tail is wasteful once a symbol has
        years of dailies cached.
        """
        rows = (
            await self.session.execute(
                select(PriceBar.date, PriceBar.close)
                .where(PriceBar.symbol == symbol)
                .order_by(PriceBar.date.desc())
                .limit(limit)
            )
        ).all()
        return [(d, float(c)) for d, c in reversed(rows)]

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

    async def bulk_upsert(
        self, symbol: str, points: list[tuple[date_type, float]], *, overwrite: bool = False
    ) -> int:
        """Insert new (date, close) points; skip dates already cached.

        ``overwrite`` updates the close on a date already held instead of
        skipping it. Needed for today's bar: while the session is open its
        "close" is a moving snapshot, so a re-fetch is a correction of the row
        rather than a duplicate of it. Off by default — a historical close does
        not change, and re-writing the backfill on every sync would be pure
        write traffic.
        """
        if not points:
            return 0
        result = await self.session.execute(
            select(PriceBar).where(
                PriceBar.symbol == symbol,
                PriceBar.date >= min(d for d, _ in points),
            )
        )
        existing = {row.date: row for row in result.scalars().all()}
        written = 0
        seen: set[date_type] = set()
        for d, close in points:
            # A provider that repeats a date within one batch would otherwise
            # insert it twice and break the unique constraint — or worse, on a
            # backend without one, silently duplicate a day in the series.
            if d in seen:
                continue
            seen.add(d)
            row = existing.get(d)
            if row is not None:
                if overwrite and row.close != close:
                    row.close = close
                    written += 1
                continue
            self.session.add(PriceBar(symbol=symbol, date=d, close=close))
            written += 1
        await self.session.flush()
        return written
