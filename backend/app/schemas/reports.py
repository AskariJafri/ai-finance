from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.dashboard import CategorySpend


class MonthReport(BaseModel):
    month: str  # "YYYY-MM"
    income: float  # salary only, attributed to the payday's month
    other_income: float  # refunds, benefits, gifts, one-off deposits
    spending: float
    net: float  # income + other_income - spending
    savings_rate: float  # 0..1 of all money in, 0 when nothing came in
    by_category: list[CategorySpend]


class MonthlyReportResponse(BaseModel):
    months: list[MonthReport]


class BucketStat(BaseModel):
    amount: float  # savings may be negative (overspent month)
    share: float  # amount / income, 0 when no income
    target: float  # 0.5 / 0.3 / 0.2
    target_amount: float  # target x income, in dollars
    # target_amount - amount. For needs/wants this is headroom still available
    # to spend; for savings (a residual, not a spend bucket) it reads the
    # opposite way — a shortfall against the goal. Same arithmetic definition
    # for all three on purpose (see ReportsService.budget_rule), so a bucket
    # can't silently flip sign in figures_for_advice's LLM payload; the UI picks
    # the wording per bucket.
    remaining: float


class BudgetRuleReport(BaseModel):
    month: str
    # Salary. The 50/30/20 targets are all percentages of this, which is why
    # other_income is reported beside it rather than folded into it: a one-off
    # deposit should not raise the bar for what you're allowed to spend.
    income: float
    detected_income: float
    other_income: float
    income_source: Literal["detected", "override"]
    needs: BucketStat
    wants: BucketStat
    savings: BucketStat
    uncategorized_amount: float
