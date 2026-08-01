"""Pay-schedule fitting and bi-weekly income attribution."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.analytics.reports import ReportsService
from app.db.models import Account, PlaidItem, Transaction
from app.domain.income_periods import (
    Cadence,
    PaySchedule,
    attributed_month,
    detect_cadence,
    fit_schedule,
    nominal_payday,
)
from app.repositories.categories import CategoryRepository
from app.repositories.users import UserRepository

# Every other Friday, ending on Friday 1 January 2027 — which is a bank
# holiday, so that cheque is paid on Thursday 31 December instead. This is the
# reported bug in its natural habitat: December looks like it received three
# cheques and January one.
NEW_YEAR_PAYDAY = date(2027, 1, 1)
BIWEEKLY_FRIDAYS = [NEW_YEAR_PAYDAY - timedelta(days=14 * i) for i in range(7, -1, -1)]
PAID_EARLY_FOR_NEW_YEAR = BIWEEKLY_FRIDAYS[:-1] + [date(2026, 12, 31)]


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
    at 15 — this is the case that must NOT get a 14-day schedule fitted to it,
    since the 1st-of-month cheque genuinely belongs to that month."""
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


# --- schedule fitting ---


def test_fits_phase_that_reproduces_every_payday():
    schedule = fit_schedule(BIWEEKLY_FRIDAYS)
    assert schedule.is_biweekly
    assert [nominal_payday(d, schedule) for d in BIWEEKLY_FRIDAYS] == BIWEEKLY_FRIDAYS


def test_one_early_deposit_does_not_drag_the_fit():
    """Seven deposits on schedule and one paid a day early for a holiday. The
    fit must follow the seven, so the early one is recognised *as* early rather
    than redefining payday for everybody."""
    schedule = fit_schedule(PAID_EARLY_FOR_NEW_YEAR)
    assert nominal_payday(date(2026, 12, 31), schedule) == NEW_YEAR_PAYDAY
    # The on-schedule deposits are left exactly where they are.
    for on in BIWEEKLY_FRIDAYS[:-1]:
        assert nominal_payday(on, schedule) == on


def test_non_biweekly_gets_no_schedule():
    schedule = fit_schedule([date(2026, m, 1) for m in range(1, 8)])
    assert not schedule.is_biweekly
    assert schedule.phase is None


# --- attribution ---


def test_cheque_paid_early_across_month_end_belongs_to_the_next_month():
    """The reported bug: the New Year's Day cheque clears on 31 December, and
    must still count as January's."""
    schedule = fit_schedule(PAID_EARLY_FOR_NEW_YEAR)
    assert attributed_month(date(2026, 12, 31), schedule) == (2027, 1)


def test_deposits_on_their_own_payday_stay_put():
    schedule = fit_schedule(BIWEEKLY_FRIDAYS)
    assert attributed_month(date(2026, 12, 4), schedule) == (2026, 12)
    assert attributed_month(date(2026, 12, 18), schedule) == (2026, 12)


def test_non_biweekly_is_never_shifted():
    monthly = fit_schedule([date(2026, m, 1) for m in range(1, 8)])
    assert attributed_month(date(2026, 7, 1), monthly) == (2026, 7)
    assert attributed_month(date(2026, 7, 1), PaySchedule(Cadence.OTHER)) == (2026, 7)


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


async def _seed_pay(session, pay_dates: list[date], cents: int = -1600_00) -> uuid.UUID:
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, account_id = await _seed(session)
    for on in pay_dates:
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account_id,
                name="PAYROLL DIRECT DEPOSIT",
                amount_cents=cents,
                date=on,
                category_id=slugs["salary"].id,
            )
        )
    await session.commit()
    return user_id


@pytest.mark.asyncio
async def test_holiday_cheque_is_not_counted_in_the_month_it_cleared(seeded_session):
    """December's income must not include the cheque that was only paid on the
    31st because New Year's Day was a holiday — that one is January's."""
    session = seeded_session
    user_id = await _seed_pay(session, PAID_EARLY_FOR_NEW_YEAR)
    service = ReportsService(session)

    december = await service.budget_rule(user_id, "2026-12")
    assert december.detected_income == 3200.0  # the 4th and the 18th, not 4800
    assert december.needs.target_amount == 1600.0  # 50% of 3200, not of 4800

    january = await service.budget_rule(user_id, "2027-01")
    assert january.detected_income == 1600.0  # the cheque that cleared 31 Dec


@pytest.mark.asyncio
async def test_a_genuine_third_cheque_stays_in_its_month(seeded_session):
    """26 cheques don't divide into 12 months, so twice a year a month really
    does receive three. Attribution must not invent a reason to move one of
    them: July 2026 has paydays on the 3rd, 17th and 31st and keeps all three."""
    session = seeded_session
    july_paydays = [date(2026, 7, 3), date(2026, 7, 17), date(2026, 7, 31)]
    earlier = [date(2026, 7, 3) - timedelta(days=14 * i) for i in range(6, 0, -1)]
    user_id = await _seed_pay(session, earlier + july_paydays)

    july = await ReportsService(session).budget_rule(user_id, "2026-07")
    assert july.detected_income == 4800.0


@pytest.mark.asyncio
async def test_monthly_earner_keeps_first_of_month_income(seeded_session):
    """Paid once a month on the 1st. Nothing may be shifted — otherwise every
    month would report zero income and the whole rule would collapse."""
    session = seeded_session
    user_id = await _seed_pay(session, [date(2026, m, 1) for m in (3, 4, 5, 6, 7)], cents=-4000_00)

    rule = await ReportsService(session).budget_rule(user_id, "2026-07")
    assert rule.detected_income == 4000.0


@pytest.mark.asyncio
async def test_income_override_still_wins(seeded_session):
    """Attribution changes what's *detected*; an explicit override must still
    take precedence over it."""
    session = seeded_session
    user_id = await _seed_pay(session, [date(2026, 7, 1), date(2026, 7, 15), date(2026, 7, 30)])

    rule = await ReportsService(session).budget_rule(
        user_id, "2026-07", income_override_cents=5000_00
    )
    assert rule.income == 5000.0
    assert rule.income_source == "override"
