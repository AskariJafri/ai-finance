from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import TransactionOut


class CategorySpend(BaseModel):
    category: str
    color: str | None = None
    amount: float


class DashboardSummary(BaseModel):
    period_start: str
    period_end: str
    period_label: str
    # Net worth: cash on hand minus card/loan debt. Kept as "total_balance" for
    # API stability, but its meaning changed — see cash_balance/credit_balance
    # for the two figures it's made of.
    total_balance: float
    cash_balance: float
    credit_balance: float
    period_spending: float
    period_income: float
    net_cash_flow: float
    savings_rate: float  # 0..1
    uncategorized_count: int
    spend_by_category: list[CategorySpend]
    recent_transactions: list[TransactionOut]
