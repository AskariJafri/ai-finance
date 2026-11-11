from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActionPlanRun, CommitteeVerdictCache


def market_period(now: datetime | None = None) -> date:
    """The trading day a plan belongs to, on the Toronto/New York clock.

    Not ``date.today()``: a server on UTC rolls over at 7–8pm Eastern, which
    would split one evening's questions across two "days". Falls back to a
    fixed UTC-5 when the host has no tz database (a bare Windows venv).
    """
    now = now or datetime.now(UTC)
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo("America/Toronto")).date()
    except Exception:  # noqa: BLE001 — no tzdata on this host
        return now.astimezone(timezone(timedelta(hours=-5))).date()


class VerdictCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_many(
        self, user_id: uuid.UUID, symbols: list[str], *, held: bool, period: date
    ) -> dict[str, dict]:
        if not symbols:
            return {}
        rows = await self.session.execute(
            select(CommitteeVerdictCache).where(
                CommitteeVerdictCache.user_id == user_id,
                CommitteeVerdictCache.held == held,
                CommitteeVerdictCache.period == period,
                CommitteeVerdictCache.symbol.in_([s.upper() for s in symbols]),
            )
        )
        return {r.symbol: dict(r.payload) for r in rows.scalars()}

    async def put_many(
        self, user_id: uuid.UUID, verdicts: list[dict], *, held: bool, period: date
    ) -> None:
        """Store each verdict unless one is already there — the first answer of
        the day is the answer, so a concurrent writer losing the race is fine."""
        for v in verdicts:
            symbol = str(v.get("symbol") or "").upper()
            if not symbol:
                continue
            try:
                async with self.session.begin_nested():
                    self.session.add(
                        CommitteeVerdictCache(
                            user_id=user_id, symbol=symbol, held=held, period=period, payload=v
                        )
                    )
            except IntegrityError:
                continue


class ActionRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID, run_id: uuid.UUID) -> ActionPlanRun | None:
        row = await self.session.execute(
            select(ActionPlanRun).where(
                ActionPlanRun.id == run_id, ActionPlanRun.user_id == user_id
            )
        )
        return row.scalar_one_or_none()

    async def latest(self, user_id: uuid.UUID) -> ActionPlanRun | None:
        row = await self.session.execute(
            select(ActionPlanRun)
            .where(ActionPlanRun.user_id == user_id)
            .order_by(ActionPlanRun.created_at.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def running(self, user_id: uuid.UUID) -> ActionPlanRun | None:
        row = await self.session.execute(
            select(ActionPlanRun)
            .where(ActionPlanRun.user_id == user_id, ActionPlanRun.status == "running")
            .order_by(ActionPlanRun.created_at.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def reusable(
        self, user_id: uuid.UUID, input_key: str, period: date
    ) -> ActionPlanRun | None:
        row = await self.session.execute(
            select(ActionPlanRun)
            .where(
                ActionPlanRun.user_id == user_id,
                ActionPlanRun.input_key == input_key,
                ActionPlanRun.period == period,
                ActionPlanRun.status == "done",
            )
            .order_by(ActionPlanRun.created_at.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def add(self, run: ActionPlanRun) -> ActionPlanRun:
        self.session.add(run)
        await self.session.flush()
        return run
