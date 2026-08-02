"""Salary vs other income.

Modelled on a real reported case: a chequing account receiving bi-weekly
payroll alongside Employment Insurance, a mobile cheque deposit and an
e-transfer. Every one of those is money arriving, but only payroll is
something to size a monthly budget against — so only payroll may move the
50/30/20 targets.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.analytics.reports import ReportsService
from app.db.models import Account, PlaidItem, Transaction
from app.repositories.categories import CategoryRepository
from app.repositories.transactions import TransactionRepository
from app.repositories.users import UserRepository

# TD PAY, exactly 14 days apart. July receives three of them.
SALARY = [
    (date(2026, 6, 4), 8596_42),
    (date(2026, 6, 18), 2730_16),
    (date(2026, 7, 2), 2822_64),
    (date(2026, 7, 16), 2466_66),
    (date(2026, 7, 30), 2742_67),
]


async def _account(session) -> tuple[uuid.UUID, uuid.UUID]:
    user = await UserRepository(session).create(
        email=f"u{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    item = PlaidItem(
        user_id=user.id,
        plaid_item_id=f"item-{uuid.uuid4().hex[:8]}",
        access_token_encrypted="enc",
    )
    session.add(item)
    await session.flush()
    account = Account(
        user_id=user.id,
        plaid_item_id=item.id,
        plaid_account_id=f"acc-{uuid.uuid4().hex[:8]}",
        name="Chequing",
        type="depository",
    )
    session.add(account)
    await session.flush()
    return user.id, account.id


async def _seed_real_shaped_month(session):
    """The reported account: payroll, EI, a mobile deposit and an e-transfer."""
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, account_id = await _account(session)

    def add(name: str, on: date, cents: int, slug: str | None):
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account_id,
                name=name,
                amount_cents=-cents,
                date=on,
                category_id=slugs[slug].id if slug else None,
            )
        )

    for on, cents in SALARY:
        add("TD PAY", on, cents, "salary")
    add("EMPL INS EI", date(2026, 7, 6), 1286_00, "income-other")
    add("MOBILE DEPOSIT", date(2026, 7, 22), 3394_83, "income-other")
    # Deliberately uncategorized: an inflow nobody has classified yet.
    add("RANDOM CREDIT", date(2026, 7, 24), 500_00, None)
    await session.commit()
    return user_id


@pytest.mark.asyncio
async def test_only_salary_drives_the_budget_targets(seeded_session):
    session = seeded_session
    user_id = await _seed_real_shaped_month(session)

    july = await ReportsService(session).budget_rule(user_id, "2026-07")

    # The three TD PAY cheques, and nothing else.
    assert july.detected_income == pytest.approx(8031.97)
    # Half a cent exactly; Python rounds the tie to even, so 4015.98 not .99.
    assert july.needs.target_amount == pytest.approx(4015.98)
    # EI + mobile deposit + the uncategorized inflow, reported but not budgeted.
    assert july.other_income == pytest.approx(5180.83)


@pytest.mark.asyncio
async def test_uncategorized_inflow_is_other_income_not_salary(seeded_session):
    """Failing open toward salary would inflate all three targets off a deposit
    nobody has identified. It must fail toward other income instead."""
    session = seeded_session
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, account_id = await _account(session)
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            name="MYSTERY CREDIT",
            amount_cents=-2000_00,
            date=date(2026, 7, 10),
            category_id=None,
        )
    )
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            name="TD PAY",
            amount_cents=-1000_00,
            date=date(2026, 7, 15),
            category_id=slugs["salary"].id,
        )
    )
    await session.commit()

    july = await ReportsService(session).budget_rule(user_id, "2026-07")
    assert july.detected_income == pytest.approx(1000.0)
    assert july.other_income == pytest.approx(2000.0)


@pytest.mark.asyncio
async def test_salary_and_other_income_partition_total_income(seeded_session):
    """The two figures must add back up to what actually arrived — otherwise
    money has gone missing between the reports."""
    session = seeded_session
    user_id = await _seed_real_shaped_month(session)
    txns = TransactionRepository(session)
    start, end = date(2026, 7, 1), date(2026, 7, 31)

    total = await txns.sum_income(user_id, start, end)
    salary = sum(cents for _on, cents in await txns.salary_rows(user_id, start, end))
    other = await txns.sum_other_income(user_id, start, end)

    assert salary + other == total


@pytest.mark.asyncio
async def test_other_income_still_counts_toward_net_cash(seeded_session):
    """Kept out of the *targets*, but it is real money — net and savings rate
    are cash questions and must include it."""
    session = seeded_session
    user_id = await _seed_real_shaped_month(session)
    txns = TransactionRepository(session)

    other = await txns.sum_other_income(user_id, date(2026, 7, 1), date(2026, 7, 31))
    assert other == 1286_00 + 3394_83 + 500_00


@pytest.mark.asyncio
async def test_non_salary_deposits_do_not_pollute_the_pay_schedule(seeded_session):
    """EI lands every two weeks too. If it were fed into the cadence fit it
    would compete with real payroll for the schedule's phase."""
    session = seeded_session
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, account_id = await _account(session)

    for on, cents in SALARY:
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account_id,
                name="TD PAY",
                amount_cents=-cents,
                date=on,
                category_id=slugs["salary"].id,
            )
        )
    # Bi-weekly EI on the alternating week, offset 7 days from payroll.
    for i in range(6):
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account_id,
                name="EMPL INS EI",
                amount_cents=-1286_00,
                date=date(2026, 6, 11) + timedelta(days=14 * i),
                category_id=slugs["income-other"].id,
            )
        )
    await session.commit()

    july = await ReportsService(session).budget_rule(user_id, "2026-07")
    # Still exactly the three TD PAY cheques.
    assert july.detected_income == pytest.approx(8031.97)
