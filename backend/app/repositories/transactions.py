from __future__ import annotations

import uuid
from datetime import date as date_type

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Account, Category, Transaction
from app.domain.accounts import LIABILITY_ACCOUNT_TYPES
from app.domain.categories import SALARY_SLUG
from app.domain.enums import CategoryGroup, CategorySource
from app.domain.transfer_matching import Leg

__all__ = ["LIABILITY_ACCOUNT_TYPES", "TransactionRepository"]


class TransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- basic CRUD ---
    async def get_by_id(self, txn_id: uuid.UUID) -> Transaction | None:
        return await self.session.get(Transaction, txn_id)

    async def get_by_plaid_id(self, plaid_transaction_id: str) -> Transaction | None:
        result = await self.session.execute(
            select(Transaction).where(Transaction.plaid_transaction_id == plaid_transaction_id)
        )
        return result.scalar_one_or_none()

    async def get_transfer_partner(
        self, transfer_group_id: uuid.UUID, exclude_id: uuid.UUID
    ) -> Transaction | None:
        """The other leg of a matched pair, if any."""
        result = await self.session.execute(
            select(Transaction).where(
                Transaction.transfer_group_id == transfer_group_id,
                Transaction.id != exclude_id,
            )
        )
        return result.scalar_one_or_none()

    async def delete_by_plaid_id(self, plaid_transaction_id: str) -> None:
        txn = await self.get_by_plaid_id(plaid_transaction_id)
        if txn is not None:
            await self.session.delete(txn)

    async def add(self, txn: Transaction) -> Transaction:
        self.session.add(txn)
        await self.session.flush()
        return txn

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> list[Transaction]:
        result = await self.session.execute(
            self._base(user_id)
            .options(
                selectinload(Transaction.category),
                # The client mirrors the income guard, which needs the account
                # type, so it ships on every row. One extra query per batch.
                selectinload(Transaction.account),
            )
            .order_by(Transaction.date.desc(), Transaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    def _base(self, user_id: uuid.UUID) -> Select[tuple[Transaction]]:
        return select(Transaction).where(Transaction.user_id == user_id)

    # --- aggregates for the dashboard/analytics (no AI, pure SQL) ---

    def _flows_where(self, user_id: uuid.UUID, start: date_type, end: date_type):
        """The predicates EVERY money aggregate must share.

        Spending and income used to be written out longhand in five places, and
        drifted: the income query grew a liability-account guard that the four
        spending queries never got. Anything common lives here now so they cannot
        diverge again.

        Two independent transfer guards, deliberately:

        * ``is_transfer`` — the persisted write-time verdict. The only thing that
          can exclude the outflow leg of a card payment, which is a positive
          amount on a depository account and therefore indistinguishable from a
          debit purchase by SQL alone.
        * ``Category.group != 'transfer'`` — still checked, so re-categorizing a
          row into a transfer category takes effect immediately even if the flag
          was never rewritten.

        ``coalesce(Category.group, 'expense')`` keeps uncategorized rows failing
        *open* toward spending: better to over-report spend than to have money
        vanish from the totals.
        """
        return (
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date <= end,
            Transaction.is_transfer.is_(False),
            func.coalesce(Category.group, CategoryGroup.EXPENSE.value)
            != CategoryGroup.TRANSFER.value,
        )

    def _flows_select(self, columns: tuple, user_id: uuid.UUID, start: date_type, end: date_type):
        """A flows query over ``columns``, joined and filtered identically.

        The ``Account`` join is unconditional — an inner join on a NOT NULL FK, so
        it changes no result — purely so the income query's extra predicate is a
        one-line addition rather than a different query shape.

        Columns are passed in rather than swapped out of a prebuilt ``select()``
        afterwards, because ``with_only_columns()`` recomputes the FROM list from
        the new columns clause and that is far too subtle a thing to be load-bearing
        for the app's money math.
        """
        return (
            select(*columns)
            .select_from(Transaction)
            .join(Account, Transaction.account_id == Account.id)
            .outerjoin(Category, Transaction.category_id == Category.id)
            .where(*self._flows_where(user_id, start, end))
        )

    async def sum_spending(self, user_id: uuid.UUID, start: date_type, end: date_type) -> int:
        """Sum of outflows (amount > 0), excluding transfers. Returns cents.

        Note there is NO liability-account predicate here, and adding one would be
        a serious bug: a purchase on a credit card is a positive amount on a
        liability account, so excluding liability rows would delete most users'
        entire spend. The card *payment* is excluded by ``is_transfer`` instead.
        """
        stmt = self._flows_select(
            (func.coalesce(func.sum(Transaction.amount_cents), 0),), user_id, start, end
        ).where(Transaction.amount_cents > 0)
        return int((await self.session.execute(stmt)).scalar_one())

    async def sum_income(self, user_id: uuid.UUID, start: date_type, end: date_type) -> int:
        """Sum of inflows (amount < 0) as a positive number, excluding transfers
        and inflows into liability accounts (credit-card / loan payments).

        The liability guard is kept even though ``is_transfer`` now also excludes
        the inbound leg of a card payment: money arriving on a liability account
        is *structurally* never earnings, so it is the safety net for when
        transfer flagging fails. Two independent guards on the same row is the point.

        Known residual: this also excludes a genuine merchant refund on a card,
        which correctly isn't income but also never reduces spending. Netting
        refunds against spend is a separate feature.
        """
        stmt = self._flows_select(
            (func.coalesce(func.sum(-Transaction.amount_cents), 0),), user_id, start, end
        ).where(*self._income_where())
        return int((await self.session.execute(stmt)).scalar_one())

    def _income_where(self):
        """Predicates every income aggregate shares, on top of ``_flows_where``.

        Split out so :meth:`sum_income`, :meth:`salary_rows` and
        :meth:`sum_other_income` cannot drift apart the way the spending and
        income queries once did — the three must partition the same set of rows
        exactly, or money appears or vanishes between the reports.
        """
        return (
            Transaction.amount_cents < 0,
            func.coalesce(Account.type, "").notin_(LIABILITY_ACCOUNT_TYPES),
        )

    async def salary_rows(
        self, user_id: uuid.UUID, start: date_type, end: date_type
    ) -> list[tuple[date_type, int]]:
        """``[(date, cents)]``, one row per pay date, for **salary only**.

        Same guards as :meth:`sum_income`, narrowed to the salary category, and
        without the final SUM so the caller can attribute each cheque to the
        payday it was paid against (see :mod:`app.domain.income_periods`).

        Salary-only matters twice over. It keeps a tax refund or a transfer
        from a friend out of the figure that sizes the 50/30/20 targets, and it
        keeps those same one-off deposits out of the pay-cadence fit — a run of
        EI payments landing every two weeks alongside real payroll would
        otherwise look like part of the same schedule.
        """
        stmt = (
            self._flows_select(
                (Transaction.date, func.sum(-Transaction.amount_cents)), user_id, start, end
            )
            .where(*self._income_where(), Category.slug == SALARY_SLUG)
            .group_by(Transaction.date)
            .order_by(Transaction.date)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(on, int(total)) for on, total in rows]

    async def sum_other_income(self, user_id: uuid.UUID, start: date_type, end: date_type) -> int:
        """Every non-salary inflow: refunds, benefits, gifts, one-off deposits.

        Uncategorized inflows land here rather than in salary. That is the safe
        direction — an unrecognised deposit that got treated as pay would
        inflate all three budget targets, whereas one treated as other income is
        merely reported next to them.
        """
        stmt = self._flows_select(
            (func.coalesce(func.sum(-Transaction.amount_cents), 0),), user_id, start, end
        ).where(
            *self._income_where(),
            func.coalesce(Category.slug, "") != SALARY_SLUG,
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def spend_by_category(
        self, user_id: uuid.UUID, start: date_type, end: date_type
    ) -> list[tuple[str, str | None, int]]:
        """Returns [(category_name, color, total_cents)] for outflows in range."""
        stmt = (
            self._flows_select(
                (
                    func.coalesce(Category.name, "Uncategorized"),
                    Category.color,
                    func.sum(Transaction.amount_cents),
                ),
                user_id,
                start,
                end,
            )
            .where(Transaction.amount_cents > 0)
            .group_by(Category.name, Category.color)
            .order_by(func.sum(Transaction.amount_cents).desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [(name, color, int(total)) for name, color, total in rows]

    async def monthly_flows(
        self, user_id: uuid.UUID, start: date_type, end: date_type
    ) -> list[tuple[int, int, int, int]]:
        """Returns [(year, month, spending_cents, income_cents)] per calendar
        month, ascending, excluding transfers. ``extract`` compiles on both
        Postgres and SQLite, so tests exercise the same query as prod."""
        year = func.extract("year", Transaction.date)
        month = func.extract("month", Transaction.date)
        stmt = (
            select(
                year,
                month,
                func.coalesce(
                    func.sum(
                        case(
                            (Transaction.amount_cents > 0, Transaction.amount_cents),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    Transaction.amount_cents < 0,
                                    func.coalesce(Account.type, "").notin_(LIABILITY_ACCOUNT_TYPES),
                                ),
                                -Transaction.amount_cents,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .select_from(Transaction)
            .join(Account, Transaction.account_id == Account.id)
            .outerjoin(Category, Transaction.category_id == Category.id)
            # A transfer is neither spending nor income, so the shared guards
            # cover both CASE columns at once.
            .where(*self._flows_where(user_id, start, end))
            .group_by(year, month)
            .order_by(year, month)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(int(y), int(m), int(sp), int(inc)) for y, m, sp, inc in rows]

    async def spend_by_slug(
        self, user_id: uuid.UUID, start: date_type, end: date_type
    ) -> list[tuple[str | None, str | None, int]]:
        """Returns [(category_slug | None, budget_bucket | None, total_cents)] for
        outflows in range, excluding transfers. None slug = uncategorized.

        ``budget_bucket`` rides along so the 50/30/20 report can bucket
        user-created categories without a second query.
        """
        stmt = (
            self._flows_select(
                (
                    Category.slug,
                    Category.budget_bucket,
                    func.sum(Transaction.amount_cents),
                ),
                user_id,
                start,
                end,
            )
            .where(Transaction.amount_cents > 0)
            .group_by(Category.slug, Category.budget_bucket)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(slug, bucket, int(total)) for slug, bucket, total in rows]

    # --- transfer pairing ---
    async def pairing_candidates(
        self, user_id: uuid.UUID, start: date_type, end: date_type
    ) -> list[Leg]:
        """Rows the pair matcher may consider, as plain domain tuples.

        Filtering happens in :func:`app.domain.transfer_matching.find_pairs`; this
        just reads the window. Rows already in a pair are included so the matcher
        can see (and skip) them.
        """
        # The "pinned by the user" verdict is computed as a boolean IN SQL
        # (rather than fetching category_source + group and comparing in
        # Python) so the result column is a plain boolean — selecting the raw
        # enum-typed Category.group value as an output column trips SQLAlchemy's
        # Enum result-processor over the coalesce()'d literal fallback.
        pinned_non_transfer = (Transaction.category_source == CategorySource.USER) & (
            func.coalesce(Category.group, CategoryGroup.EXPENSE.value)
            != CategoryGroup.TRANSFER.value
        )
        stmt = (
            select(
                Transaction.id,
                Transaction.account_id,
                Transaction.amount_cents,
                Transaction.date,
                Transaction.currency,
                Transaction.transfer_group_id,
                pinned_non_transfer,
            )
            .select_from(Transaction)
            .outerjoin(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.user_id == user_id,
                Transaction.date >= start,
                Transaction.date <= end,
            )
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            Leg(
                id=row[0],
                account_id=row[1],
                amount_cents=int(row[2]),
                date=row[3],
                currency=row[4],
                has_group=row[5] is not None,
                pinned_non_transfer=bool(row[6]),
            )
            for row in rows
        ]

    async def mark_transfer_pair(
        self,
        *,
        outflow_id: uuid.UUID,
        inflow_id: uuid.UUID,
        group_id: uuid.UUID,
        category_id: uuid.UUID | None,
    ) -> None:
        """Flag both legs of a matched pair, sharing one ``transfer_group_id``.

        Only fills in a category for legs that don't have a real one yet — a leg
        the user or a rule already categorized keeps its category; the flag alone
        is what removes it from the totals.
        """
        for txn_id in (outflow_id, inflow_id):
            txn = await self.session.get(Transaction, txn_id)
            if txn is None:
                continue
            txn.is_transfer = True
            txn.transfer_group_id = group_id
            if category_id is not None and txn.category_source in (
                CategorySource.UNCATEGORIZED,
                CategorySource.AI,
            ):
                txn.category_id = category_id
                txn.category_source = CategorySource.RULE
                txn.category_confidence = 0.9

    async def count_uncategorized(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(
            Transaction.user_id == user_id,
            Transaction.category_source == CategorySource.UNCATEGORIZED,
        )
        return int((await self.session.execute(stmt)).scalar_one())
