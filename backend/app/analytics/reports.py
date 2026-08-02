"""Monthly reports and the 50/30/20 budget rule — pure SQL aggregates.

Same guardrail as the dashboard: every figure comes from repository queries
over real data. The AI layer only ever explains these numbers (see
``figures_for_advice``), never produces them.
"""

from __future__ import annotations

import calendar
import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.budget_rule import Bucket, bucket_for
from app.domain.income_periods import MAX_SHIFT_DAYS, attributed_month, fit_schedule
from app.repositories.transactions import TransactionRepository
from app.schemas.common import cents_to_dollars
from app.schemas.dashboard import CategorySpend
from app.schemas.reports import (
    BucketStat,
    BudgetRuleReport,
    MonthlyReportResponse,
    MonthReport,
)


def month_bounds(month: str) -> tuple[date, date]:
    """First and last day of a "YYYY-MM" month."""
    year, mon = int(month[:4]), int(month[5:7])
    return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])


def _shift_months(anchor: date, back: int) -> date:
    """First day of the month ``back`` months before anchor's month."""
    idx = anchor.year * 12 + (anchor.month - 1) - back
    return date(idx // 12, idx % 12 + 1, 1)


# How much history to look back over when fitting the pay schedule. Long
# enough to see a dozen bi-weekly cheques, so one odd month can't flip it.
CADENCE_HISTORY_DAYS = 180


class ReportsService:
    def __init__(self, session: AsyncSession) -> None:
        self.txns = TransactionRepository(session)

    async def _salary_by_month(
        self, user_id: uuid.UUID, start: date, end: date
    ) -> dict[tuple[int, int], int]:
        """Salary cents keyed by the ``(year, month)`` it's attributed to.

        Bi-weekly pay doesn't divide evenly into calendar months, and a deposit
        gets moved off its payday whenever that payday falls on a weekend or a
        holiday. So each deposit is counted in the month of the payday it was
        paid *against* — see :mod:`app.domain.income_periods`.

        Looks back far enough to fit the schedule, and pads both ends of the
        range by ``MAX_SHIFT_DAYS`` so a deposit that cleared just outside the
        requested months can still be attributed into them.

        Salary only — everything else that arrives is real money but is not
        something to size a monthly budget against, and is reported separately
        as other income.
        """
        rows = await self.txns.salary_rows(
            user_id,
            start - timedelta(days=CADENCE_HISTORY_DAYS),
            end + timedelta(days=MAX_SHIFT_DAYS),
        )
        schedule = fit_schedule([on for on, _cents in rows])

        totals: dict[tuple[int, int], int] = defaultdict(int)
        for on, cents in rows:
            totals[attributed_month(on, schedule)] += cents
        return totals

    async def monthly_report(self, user_id: uuid.UUID, months: int = 6) -> MonthlyReportResponse:
        today = date.today()
        start = _shift_months(today, months - 1)
        flows = await self.txns.monthly_flows(user_id, start, today)
        # Income comes from the payday attribution, not the calendar-month
        # column in ``flows``, so this chart agrees with the 50/30/20 report.
        income_by_month = await self._salary_by_month(user_id, start, today)

        out: list[MonthReport] = []
        for year, mon, spending_cents, _calendar_income_cents in flows:
            income_cents = income_by_month.get((year, mon), 0)
            month = f"{year:04d}-{mon:02d}"
            m_start, m_end = month_bounds(month)
            by_category = await self.txns.spend_by_category(user_id, m_start, m_end)
            other_cents = await self.txns.sum_other_income(user_id, m_start, m_end)
            # Net and savings rate are cash questions — what actually landed
            # against what actually went out — so other income belongs in them
            # even though it is deliberately kept out of the budget targets.
            net_cents = income_cents + other_cents - spending_cents
            total_in_cents = income_cents + other_cents
            out.append(
                MonthReport(
                    month=month,
                    income=cents_to_dollars(income_cents),
                    other_income=cents_to_dollars(other_cents),
                    spending=cents_to_dollars(spending_cents),
                    net=cents_to_dollars(net_cents),
                    savings_rate=(
                        round(net_cents / total_in_cents, 4) if total_in_cents > 0 else 0.0
                    ),
                    by_category=[
                        CategorySpend(category=name, color=color, amount=cents_to_dollars(total))
                        for name, color, total in by_category
                    ],
                )
            )
        return MonthlyReportResponse(months=out)

    async def budget_rule(
        self,
        user_id: uuid.UUID,
        month: str,
        income_override_cents: int | None = None,
    ) -> BudgetRuleReport:
        start, end = month_bounds(month)
        # Not sum_income over the calendar month, on two counts: that would
        # count a tax refund or an e-transfer from a friend as pay, and a
        # bi-weekly earner whose next cheque was paid a day early — before the
        # month turned over — would show three cheques here and get every
        # target inflated by half.
        by_month = await self._salary_by_month(user_id, start, end)
        detected_cents = by_month.get((start.year, start.month), 0)
        income_cents = (
            income_override_cents if income_override_cents is not None else detected_cents
        )
        other_income_cents = await self.txns.sum_other_income(user_id, start, end)

        needs_cents = 0
        wants_cents = 0
        uncategorized_cents = 0
        for slug, budget_bucket, total in await self.txns.spend_by_slug(user_id, start, end):
            if bucket_for(slug, budget_bucket) is Bucket.NEEDS:
                needs_cents += total
            else:
                wants_cents += total
            if slug is None or slug == "uncategorized":
                uncategorized_cents += total

        savings_cents = income_cents - needs_cents - wants_cents

        def stat(amount_cents: int, target: float) -> BucketStat:
            target_cents = round(income_cents * target)
            return BucketStat(
                amount=cents_to_dollars(amount_cents),
                share=(round(amount_cents / income_cents, 4) if income_cents > 0 else 0.0),
                target=target,
                target_amount=cents_to_dollars(target_cents),
                remaining=cents_to_dollars(target_cents - amount_cents),
            )

        return BudgetRuleReport(
            month=month,
            income=cents_to_dollars(income_cents),
            detected_income=cents_to_dollars(detected_cents),
            other_income=cents_to_dollars(other_income_cents),
            income_source=("override" if income_override_cents is not None else "detected"),
            needs=stat(needs_cents, 0.5),
            wants=stat(wants_cents, 0.3),
            savings=stat(savings_cents, 0.2),
            uncategorized_amount=cents_to_dollars(uncategorized_cents),
        )

    async def figures_for_advice(
        self,
        user_id: uuid.UUID,
        month: str,
        income_override_cents: int | None = None,
    ) -> dict:
        """Pre-computed figures the advice prompt is built from (pure SQL)."""
        rule = await self.budget_rule(user_id, month, income_override_cents)
        start, end = month_bounds(month)

        prev_month = _shift_months(start, 1)
        prev_key = f"{prev_month.year:04d}-{prev_month.month:02d}"
        prev_start, prev_end = month_bounds(prev_key)
        prev_spending_cents = await self.txns.sum_spending(user_id, prev_start, prev_end)

        top_categories = (await self.txns.spend_by_category(user_id, start, end))[:5]
        uncategorized_count = await self.txns.count_uncategorized(user_id)

        return {
            "month": month,
            "income": rule.income,
            "income_source": rule.income_source,
            "spending": round(rule.needs.amount + rule.wants.amount, 2),
            "prev_month_spending": cents_to_dollars(prev_spending_cents),
            "needs": rule.needs.model_dump(),
            "wants": rule.wants.model_dump(),
            "savings": rule.savings.model_dump(),
            "top_categories": [
                {"category": name, "amount": cents_to_dollars(total)}
                for name, _color, total in top_categories
            ],
            "uncategorized_amount": rule.uncategorized_amount,
            "uncategorized_count": uncategorized_count,
        }
