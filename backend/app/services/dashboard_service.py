"""Dashboard summary — computed purely from SQL aggregates (never the LLM).

This is the guardrail from the plan: every figure the user sees is derived from
repository queries over their real data. The AI layer is only ever asked to
*explain* these numbers, not produce them.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.accounts import CASH_ACCOUNT_TYPES, LIABILITY_ACCOUNT_TYPES
from app.repositories.accounts import AccountRepository
from app.repositories.transactions import TransactionRepository
from app.schemas.common import TransactionOut, cents_to_dollars
from app.schemas.dashboard import CategorySpend, DashboardSummary

PERIOD_LABEL = "Last 30 days"


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.accounts = AccountRepository(session)
        self.txns = TransactionRepository(session)

    async def summary(self, user_id: uuid.UUID) -> DashboardSummary:
        # Rolling 30-day window rather than calendar month-to-date: the dashboard
        # stays populated regardless of the day of the month or gaps in recent
        # data (e.g. Plaid Sandbox datasets that trail the current date). Do NOT
        # switch this to calendar MTD to match the budget-rule report — that
        # would make the dashboard read ~$0 on the 1st of every month.
        today = date.today()
        start = today - timedelta(days=30)

        accounts = await self.accounts.list_for_user(user_id)
        cash_cents = sum(a.current_balance_cents for a in accounts if a.type in CASH_ACCOUNT_TYPES)
        # Plaid reports a card's balance as a positive amount owed (and a loan's
        # as positive principal outstanding), so plain subtraction is correct —
        # including for an overpaid card, which reports a negative balance and so
        # correctly *adds* back. No clamping.
        #
        # Deliberately NOT "every non-liability account": that would pull
        # Plaid-linked investment accounts into this banking headline, double
        # counting against the Invest tab, which tracks holdings separately.
        credit_cents = sum(
            a.current_balance_cents for a in accounts if (a.type or "") in LIABILITY_ACCOUNT_TYPES
        )
        net_worth_cents = cash_cents - credit_cents

        spending_cents = await self.txns.sum_spending(user_id, start, today)
        income_cents = await self.txns.sum_income(user_id, start, today)
        net_cents = income_cents - spending_cents
        savings_rate = (net_cents / income_cents) if income_cents > 0 else 0.0

        by_category = await self.txns.spend_by_category(user_id, start, today)
        recent = await self.txns.list_for_user(user_id, limit=10)
        uncategorized = await self.txns.count_uncategorized(user_id)

        return DashboardSummary(
            period_start=start.isoformat(),
            period_end=today.isoformat(),
            period_label=PERIOD_LABEL,
            total_balance=cents_to_dollars(net_worth_cents),
            cash_balance=cents_to_dollars(cash_cents),
            credit_balance=cents_to_dollars(credit_cents),
            period_spending=cents_to_dollars(spending_cents),
            period_income=cents_to_dollars(income_cents),
            net_cash_flow=cents_to_dollars(net_cents),
            savings_rate=round(max(0.0, savings_rate), 4),
            uncategorized_count=uncategorized,
            spend_by_category=[
                CategorySpend(category=name, color=color, amount=cents_to_dollars(total))
                for name, color, total in by_category
            ],
            recent_transactions=[TransactionOut.from_model(t) for t in recent],
        )
