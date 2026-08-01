"""Pay-cadence detection and bi-weekly income attribution."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.analytics.reports import ReportsService
from app.db.models import Account, PlaidItem, Transaction
from app.domain.income_periods import Cadence, attributed_month, detect_cadence
from app.repositories.categories import CategoryRepository
from app.repositories.users import UserRepository

# --- cadence detection ---


def test_detects_biweekly():
    dates = [date(2026, 5, 6) + timedelta(days=14 * i) for i in range(8)]
    assert detect_cadence(dates) is Cadence.BIWEEKLY


def test_detects_biweekly_with_holiday_slippage():
    # Real deposits drift a day or two around weekends and bank holidays.
    dates = [
        date(2026, 5, 6),
        date(2026, 5, 20),
        date(2026, 6, 3),
        date(2026, 6, 16),  # a day early
        date(2026, 7, 1),
        date(2026, 7, 15),
    ]
    assert detect_cadence(dates) is Cadence.BIWEEKLY


def test_monthly_pay_is_not_biweekly():
    dates = [date(2026, m, 1) for m in range(1, 8)]
    assert detect_cadence(dates) is Cadence.OTHER


def test_semi_monthly_pay_is_not_biweekly():
    """Paid on the 1st and 15th. Gaps alternate ~14/~16 but the median lands
    at 15 — this is the case that must NOT get shifted, since the 1st-of-month
    cheque genuinely belongs to that month."""
    dates = []
    for m in range(1, 7):
        dates.extend([date(2026, m, 1), date(2026, m, 15)])
    assert detect_cadence(dates) is Cadence.OTHER


def test_long_biweekly_history_still_detected():
    """Two years of bi-weekly pay is 52 deposits across only 31 possible
    days-of-month, so a naive distinct-days ratio would sag below the
    threshold. Detection looks at a bounded recent window to avoid that."""
    dates = [date(2024, 1, 5) + timedelta(days=14 * i) for i in range(52)]
    assert detect_cadence(dates) is Cadence.BIWEEKLY


def test_long_semi_monthly_history_still_rejected():
    dates = []
    for year in (2025, 2026):
        for m in range(1, 13):
            dates.extend([date(year, m, 1), date(year, m, 15)])
    assert detect_cadence(dates) is Cadence.OTHER


def test_too_little_history_is_not_biweekly():
    assert detect_cadence([date(2026, 7, 1), date(2026, 7, 15)]) is Cadence.OTHER


def test_no_deposits_is_not_biweekly():
    assert detect_cadence([]) is Cadence.OTHER


# --- attribution ---


def test_first_of_month_attributed_to_previous_month_when_biweekly():
    assert attributed_month(date(2026, 7, 1), Cadence.BIWEEKLY) == (2026, 6)
    assert attributed_month(date(2026, 7, 3), Cadence.BIWEEKLY) == (2026, 6)


def test_mid_and_late_month_stay_put():
    assert attributed_month(date(2026, 7, 15), Cadence.BIWEEKLY) == (2026, 7)
    assert attributed_month(date(2026, 7, 30), Cadence.BIWEEKLY) == (2026, 7)


def test_january_first_rolls_back_a_year():
    assert attributed_month(date(2026, 1, 2), Cadence.BIWEEKLY) == (2025, 12)


def test_non_biweekly_is_never_shifted():
    assert attributed_month(date(2026, 7, 1), Cadence.OTHER) == (2026, 7)


# --- end to end through the budget rule ---


async def _seed(session) -> tuple[uuid.UUID, uuid.UUID]:
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


@pytest.mark.asyncio
async def test_three_biweekly_cheques_in_july_counts_only_two(seeded_session):
    """The reported case: paid bi-weekly, July has deposits on the 1st, 15th
    and 30th. July's income must be the 15th + 30th; the 1st belongs to June."""
    session = seeded_session
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, account_id = await _seed(session)

    # A bi-weekly run long enough for the cadence to be detected, landing on
    # exactly the dates described.
    pay_dates = [
        date(2026, 4, 8),
        date(2026, 4, 22),
        date(2026, 5, 6),
        date(2026, 5, 20),
        date(2026, 6, 3),
        date(2026, 6, 17),
        date(2026, 7, 1),
        date(2026, 7, 15),
        date(2026, 7, 30),
    ]
    for on in pay_dates:
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account_id,
                name="PAYROLL DIRECT DEPOSIT",
                amount_cents=-1600_00,
                date=on,
                category_id=slugs["salary"].id,
            )
        )
    await session.commit()

    service = ReportsService(session)

    july = await service.budget_rule(user_id, "2026-07")
    assert july.detected_income == 3200.0  # 15th + 30th only, not 4800
    assert july.needs.target_amount == 1600.0  # 50% of 3200, not of 4800

    # The July 1st cheque lands in June, which keeps two of its own (June 17th
    # went to June; June 3rd rolled back to May).
    june = await service.budget_rule(user_id, "2026-06")
    assert june.detected_income == 3200.0


@pytest.mark.asyncio
async def test_monthly_earner_keeps_first_of_month_income(seeded_session):
    """Paid once a month on the 1st. Nothing may be shifted — otherwise every
    month would report zero income and the whole rule would collapse."""
    session = seeded_session
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, account_id = await _seed(session)

    for month in (3, 4, 5, 6, 7):
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account_id,
                name="PAYROLL DIRECT DEPOSIT",
                amount_cents=-4000_00,
                date=date(2026, month, 1),
                category_id=slugs["salary"].id,
            )
        )
    await session.commit()

    rule = await ReportsService(session).budget_rule(user_id, "2026-07")
    assert rule.detected_income == 4000.0


@pytest.mark.asyncio
async def test_income_override_still_wins(seeded_session):
    """Attribution changes what's *detected*; an explicit override must still
    take precedence over it."""
    session = seeded_session
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, account_id = await _seed(session)

    for on in [date(2026, 7, 1), date(2026, 7, 15), date(2026, 7, 30)]:
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account_id,
                name="PAYROLL",
                amount_cents=-1600_00,
                date=on,
                category_id=slugs["salary"].id,
            )
        )
    await session.commit()

    rule = await ReportsService(session).budget_rule(
        user_id, "2026-07", income_override_cents=5000_00
    )
    assert rule.income == 5000.0
    assert rule.income_source == "override"
