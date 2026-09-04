"""Global (cross-user) caches for the research layer.

Both repositories guard their inserts with a savepoint. The first request of
the day is the dangerous one: two concurrent plans can both miss the cache,
both INSERT, and the loser hits the unique constraint. Without the savepoint
that IntegrityError poisons the caller's session in the middle of a plan that
had nothing to do with news, and a research-layer failure would take down a
surface it is meant to be invisible to.
"""

from __future__ import annotations

from datetime import date as date_type

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MacroBriefCache, NewsSnapshot


class NewsSnapshotRepository:
    """One row per (symbol, day). Stores "we asked and got nothing" too."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_many(self, symbols: list[str], as_of: date_type) -> dict[str, NewsSnapshot]:
        if not symbols:
            return {}
        rows = (
            (
                await self.session.execute(
                    select(NewsSnapshot).where(
                        NewsSnapshot.symbol.in_([s.upper() for s in symbols]),
                        NewsSnapshot.as_of == as_of,
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.symbol: row for row in rows}

    async def upsert(
        self,
        *,
        symbol: str,
        as_of: date_type,
        coverage: str,
        article_count: int,
        flags: str,
        worst_severity: int,
        payload: dict,
        source: str = "finnhub",
    ) -> NewsSnapshot:
        symbol = symbol.upper()
        existing = (
            await self.session.execute(
                select(NewsSnapshot).where(
                    NewsSnapshot.symbol == symbol, NewsSnapshot.as_of == as_of
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.coverage = coverage
            existing.article_count = article_count
            existing.flags = flags
            existing.worst_severity = worst_severity
            existing.payload = payload
            existing.source = source
            return existing

        row = NewsSnapshot(
            symbol=symbol,
            as_of=as_of,
            coverage=coverage,
            article_count=article_count,
            flags=flags,
            worst_severity=worst_severity,
            payload=payload,
            source=source,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
        except IntegrityError:
            # Another request wrote the same (symbol, day) first. Its row is
            # as good as ours — the savepoint rolled back, so re-read.
            return (
                await self.session.execute(
                    select(NewsSnapshot).where(
                        NewsSnapshot.symbol == symbol, NewsSnapshot.as_of == as_of
                    )
                )
            ).scalar_one()
        return row

    async def prune(self, before: date_type) -> int:
        """Drop snapshots older than ``before``.

        Unlike price bars, stale news has no archival value — nothing reads a
        three-month-old headline, and the table would grow without bound.
        """
        result = await self.session.execute(delete(NewsSnapshot).where(NewsSnapshot.as_of < before))
        return result.rowcount or 0


class MacroBriefRepository:
    """One row per (day, topic) for the entire application."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, as_of: date_type, topic: str = "markets") -> MacroBriefCache | None:
        return (
            await self.session.execute(
                select(MacroBriefCache).where(
                    MacroBriefCache.as_of == as_of, MacroBriefCache.topic == topic
                )
            )
        ).scalar_one_or_none()

    async def upsert(
        self,
        *,
        as_of: date_type,
        topic: str,
        regime: str,
        summary: str,
        payload: dict,
        source: str = "tavily",
    ) -> MacroBriefCache:
        existing = await self.get(as_of, topic)
        if existing is not None:
            existing.regime = regime
            existing.summary = summary
            existing.payload = payload
            existing.source = source
            return existing

        row = MacroBriefCache(
            as_of=as_of,
            topic=topic,
            regime=regime,
            summary=summary,
            payload=payload,
            source=source,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
        except IntegrityError:
            found = await self.get(as_of, topic)
            if found is None:  # pragma: no cover - defensive
                raise
            return found
        return row

    async def prune(self, before: date_type) -> int:
        result = await self.session.execute(
            delete(MacroBriefCache).where(MacroBriefCache.as_of < before)
        )
        return result.rowcount or 0
