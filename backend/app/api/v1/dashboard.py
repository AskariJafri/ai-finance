from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DashboardServiceDep
from app.schemas.dashboard import DashboardSummary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def summary(user: CurrentUser, service: DashboardServiceDep) -> DashboardSummary:
    return await service.summary(user.id)
