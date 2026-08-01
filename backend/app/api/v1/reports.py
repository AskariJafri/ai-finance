from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, ReportsServiceDep, SettingsServiceDep
from app.schemas.reports import BudgetRuleReport, MonthlyReportResponse

router = APIRouter(prefix="/reports", tags=["reports"])

_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


@router.get("/monthly", response_model=MonthlyReportResponse)
async def monthly_report(
    user: CurrentUser,
    service: ReportsServiceDep,
    months: int = Query(default=6, ge=1, le=24),
) -> MonthlyReportResponse:
    return await service.monthly_report(user.id, months=months)


@router.get("/budget-rule", response_model=BudgetRuleReport)
async def budget_rule(
    user: CurrentUser,
    service: ReportsServiceDep,
    settings_service: SettingsServiceDep,
    month: str | None = Query(default=None, pattern=_MONTH_PATTERN),
) -> BudgetRuleReport:
    override_cents = await settings_service.get_income_override_cents(user.id)
    return await service.budget_rule(
        user.id, month or _current_month(), income_override_cents=override_cents
    )
