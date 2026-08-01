from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import AdviceServiceDep, CurrentUser
from app.schemas.advice import AdviceOut

router = APIRouter(prefix="/advice", tags=["advice"])

_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


@router.get("/monthly", response_model=AdviceOut)
async def get_monthly_advice(
    user: CurrentUser,
    service: AdviceServiceDep,
    month: str | None = Query(default=None, pattern=_MONTH_PATTERN),
) -> AdviceOut:
    """Cached advice only — fast, safe to call on screen open."""
    return await service.get(user.id, month or _current_month())


@router.post("/monthly", response_model=AdviceOut)
async def generate_monthly_advice(
    user: CurrentUser,
    service: AdviceServiceDep,
    month: str | None = Query(default=None, pattern=_MONTH_PATTERN),
    force: bool = Query(default=False),
) -> AdviceOut:
    """Generate (or regenerate with force=true). Slow — local LLM."""
    return await service.generate(user.id, month or _current_month(), force=force)
