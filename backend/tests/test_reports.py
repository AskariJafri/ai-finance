"""Monthly reports and 50/30/20 budget rule — repository + service + API."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import AsyncClient

from app.analytics.reports import ReportsService, month_bounds
from app.db.models import Account, PlaidItem, Transaction
from app.repositories.categories import CategoryRepository
from app.repositories.transactions import TransactionRepository
from app.repositories.users import UserRepository

PREFIX = "/api/v1"

# Fixed months so tests are deterministic regardless of today's date.
M1, M2 = "2026-05", "2026-06"


async def _seed_user_with_account(session) -> tuple[uuid.UUID, uuid.UUID]:
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


def _txn(user_id, account_id, on: date, cents: int, category_id=None) -> Transaction:
    return Transaction(
        user_id=user_id,
        account_id=account_id,
        name="t",
        amount_cents=cents,
        date=on,
        category_id=category_id,
    )


class FakeDate(date):
    """Anchors 'today' inside M2 so the test is deterministic."""

    @classmethod
    def today(cls):
        return cls(2026, 6, 15)


@pytest.mark.asyncio
async def test_monthly_flows_and_budget_rule(seeded_session, monkeypatch):
    session = seeded_session
    user_id, account_id = await _seed_user_with_account(session)
    slugs = await CategoryRepository(session).ensure_seed()

    txns = [
        # May: 3000 income, 1000 needs (rent), 400 wants (restaurants)
        _txn(user_id, account_id, date(2026, 5, 1), -3000_00, slugs["salary"].id),
        _txn(user_id, account_id, date(2026, 5, 3), 1000_00, slugs["rent"].id),
        _txn(user_id, account_id, date(2026, 5, 10), 400_00, slugs["restaurants"].id),
        # June: 3200 income, 900 needs (groceries), 150 uncategorized (-> wants)
        _txn(user_id, account_id, date(2026, 6, 1), -3200_00, slugs["salary"].id),
        _txn(user_id, account_id, date(2026, 6, 5), 900_00, slugs["groceries"].id),
        _txn(user_id, account_id, date(2026, 6, 8), 150_00, None),
        # Transfers must be excluded from every aggregate.
        _txn(user_id, account_id, date(2026, 6, 9), 500_00, slugs["transfer"].id),
        _txn(user_id, account_id, date(2026, 6, 9), -500_00, slugs["transfer"].id),
    ]
    session.add_all(txns)
    await session.commit()

    monkeypatch.setattr("app.analytics.reports.date", FakeDate)
    service = ReportsService(session)

    report = await service.monthly_report(user_id, months=6)
    months = {m.month: m for m in report.months}
    assert list(months) == [M1, M2]  # ascending, only months with data
    assert months[M1].income == 3000.0
    assert months[M1].spending == 1400.0
    assert months[M1].net == 1600.0
    assert months[M2].income == 3200.0
    assert months[M2].spending == 1050.0  # transfer excluded
    assert months[M2].savings_rate == pytest.approx(2150 / 3200, abs=1e-4)
    assert any(c.category == "Groceries" for c in months[M2].by_category)

    rule = await service.budget_rule(user_id, M2)
    assert rule.income == 3200.0
    assert rule.income_source == "detected"
    assert rule.needs.amount == 900.0
    assert rule.wants.amount == 150.0  # uncategorized counted as wants
    assert rule.savings.amount == 2150.0
    assert rule.uncategorized_amount == 150.0
    assert rule.needs.share == pytest.approx(900 / 3200, abs=1e-4)
    assert (rule.needs.target, rule.wants.target, rule.savings.target) == (0.5, 0.3, 0.2)

    # Manual override replaces detected income.
    rule = await service.budget_rule(user_id, M2, income_override_cents=5000_00)
    assert rule.income == 5000.0
    assert rule.detected_income == 3200.0
    assert rule.income_source == "override"

    # Zero-income month: shares guard against division by zero.
    rule = await service.budget_rule(user_id, "2026-04")
    assert rule.income == 0.0
    assert rule.needs.share == 0.0 and rule.savings.share == 0.0


@pytest.mark.asyncio
async def test_credit_card_payment_excluded_from_income(seeded_session):
    """A payment landing on a credit-card account (amount < 0) is not income —
    counting it would inflate earnings and double-count the purchases it repays.
    Credit-card *purchases* (amount > 0) still count as spending."""
    session = seeded_session
    slugs = await CategoryRepository(session).ensure_seed()
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
    chequing = Account(
        user_id=user.id,
        plaid_item_id=item.id,
        plaid_account_id=f"dep-{uuid.uuid4().hex[:8]}",
        name="Chequing",
        type="depository",
    )
    card = Account(
        user_id=user.id,
        plaid_item_id=item.id,
        plaid_account_id=f"cc-{uuid.uuid4().hex[:8]}",
        name="Visa",
        type="credit",
    )
    session.add_all([chequing, card])
    await session.flush()

    on = date(2026, 6, 10)
    session.add_all(
        [
            _txn(user.id, chequing.id, on, -3000_00, slugs["salary"].id),  # real income
            _txn(user.id, chequing.id, on, 900_00, slugs["groceries"].id),  # spending
            _txn(user.id, card.id, on, 200_00, slugs["shopping"].id),  # card purchase = spending
            _txn(user.id, card.id, on, -450_00, None),  # card PAYMENT inflow
        ]
    )
    await session.commit()

    repo = TransactionRepository(session)
    start, end = date(2026, 6, 1), date(2026, 6, 30)
    # The -450 card payment must not inflate income; only the 3000 salary counts.
    assert await repo.sum_income(user.id, start, end) == 3000_00
    # Card purchase still counts as spending (900 chequing + 200 card).
    assert await repo.sum_spending(user.id, start, end) == 1100_00
    # And the monthly-flows income column agrees.
    flows = await repo.monthly_flows(user.id, start, end)
    assert [(inc) for _, _, _, inc in flows] == [3000_00]


def test_month_bounds():
    assert month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))
    assert month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))


@pytest.mark.asyncio
async def test_reports_api_flow(client: AsyncClient):
    resp = await client.post(
        f"{PREFIX}/auth/register",
        json={"email": "rep@test.com", "password": "supersecret123"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    await client.post(
        f"{PREFIX}/plaid/link/exchange",
        headers=headers,
        json={"public_token": "public-sandbox-abc"},
    )

    monthly = await client.get(f"{PREFIX}/reports/monthly", headers=headers)
    assert monthly.status_code == 200
    months = monthly.json()["months"]
    assert len(months) >= 1
    # Mock Plaid data is clamped into the current month with 3200 income.
    assert months[-1]["income"] == 3200.0
    # Exact, not just "> 0": a regression that mirrors the liability guard onto
    # spending, or drops the transfer flag on the card-payment pair, would
    # silently change this number rather than fail loudly.
    assert months[-1]["spending"] == 514.40

    rule = await client.get(f"{PREFIX}/reports/budget-rule", headers=headers)
    assert rule.status_code == 200
    body = rule.json()
    assert body["income_source"] == "detected"
    assert body["income"] == 3200.0
    assert body["needs"]["amount"] + body["wants"]["amount"] > 0

    # Set an override and confirm the rule uses it.
    put = await client.put(
        f"{PREFIX}/settings/budget",
        headers=headers,
        json={"monthly_income_override": 5000.0},
    )
    assert put.status_code == 200
    assert put.json()["monthly_income_override"] == 5000.0

    rule = (await client.get(f"{PREFIX}/reports/budget-rule", headers=headers)).json()
    assert rule["income_source"] == "override"
    assert rule["income"] == 5000.0
    assert rule["detected_income"] == 3200.0

    # Clearing restores auto-detect.
    await client.put(
        f"{PREFIX}/settings/budget",
        headers=headers,
        json={"monthly_income_override": None},
    )
    rule = (await client.get(f"{PREFIX}/reports/budget-rule", headers=headers)).json()
    assert rule["income_source"] == "detected"

    # Bad month formats are rejected.
    bad = await client.get(f"{PREFIX}/reports/budget-rule?month=2026-13", headers=headers)
    assert bad.status_code == 422

    # The mobile app requests transactions with limit=500 — must stay accepted.
    txns = await client.get(f"{PREFIX}/transactions?limit=500", headers=headers)
    assert txns.status_code == 200


async def _seed_two_accounts(session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (user_id, chequing_id, card_id)."""
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
    chequing = Account(
        user_id=user.id,
        plaid_item_id=item.id,
        plaid_account_id=f"dep-{uuid.uuid4().hex[:8]}",
        name="Chequing",
        type="depository",
    )
    card = Account(
        user_id=user.id,
        plaid_item_id=item.id,
        plaid_account_id=f"cc-{uuid.uuid4().hex[:8]}",
        name="Visa",
        type="credit",
    )
    session.add_all([chequing, card])
    await session.flush()
    return user.id, chequing.id, card.id


@pytest.mark.asyncio
async def test_credit_card_payment_outflow_leg_excluded_from_spending(seeded_session):
    """The three-row card-payment pattern, end to end. Row 1 (card purchase) IS
    spending; rows 2 and 3 (both legs of the payment) are neither spending nor
    income — this is the case a bare liability-account guard cannot handle,
    since row 2 is a positive amount on a depository account, structurally
    identical to a debit purchase."""
    session = seeded_session
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, chequing_id, card_id = await _seed_two_accounts(session)

    on = date(2026, 6, 10)
    row1_purchase = _txn(user_id, card_id, on, 100_00, slugs["shopping"].id)
    row2_outflow = _txn(user_id, chequing_id, on, 100_00, slugs["credit-card-payment"].id)
    row2_outflow.is_transfer = True
    row3_inflow = _txn(user_id, card_id, on, -100_00, slugs["credit-card-payment"].id)
    row3_inflow.is_transfer = True
    session.add_all([row1_purchase, row2_outflow, row3_inflow])
    await session.commit()

    repo = TransactionRepository(session)
    start, end = date(2026, 6, 1), date(2026, 6, 30)
    assert await repo.sum_spending(user_id, start, end) == 100_00
    assert await repo.sum_income(user_id, start, end) == 0

    flows = await repo.monthly_flows(user_id, start, end)
    assert [(sp, inc) for _, _, sp, inc in flows] == [(100_00, 0)]

    by_category = await repo.spend_by_category(user_id, start, end)
    assert all(name != "Credit Card Payment" for name, _color, _total in by_category)

    by_slug = await repo.spend_by_slug(user_id, start, end)
    assert all(slug != "credit-card-payment" for slug, _bucket, _total in by_slug)


@pytest.mark.asyncio
async def test_credit_card_purchase_still_counts_as_spending(seeded_session):
    """The liability-account guard must NOT be mirrored onto spending — a
    purchase on a credit card (positive amount, liability account) is real
    spending, and excluding it would delete most users' entire spend."""
    session = seeded_session
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, _chequing_id, card_id = await _seed_two_accounts(session)

    on = date(2026, 6, 10)
    session.add(_txn(user_id, card_id, on, 250_00, slugs["shopping"].id))
    await session.commit()

    repo = TransactionRepository(session)
    start, end = date(2026, 6, 1), date(2026, 6, 30)
    assert await repo.sum_spending(user_id, start, end) == 250_00


@pytest.mark.asyncio
async def test_uncategorized_outflow_still_counts_as_spending(seeded_session):
    """Uncategorized outflows fail OPEN toward spending (coalesce(..., 'expense')
    in the shared query base) — a deliberate choice so unclassified spend isn't
    silently invisible to the user. Pinned here so a future "safety" change
    can't flip this by accident."""
    session = seeded_session
    await CategoryRepository(session).ensure_seed()
    user_id, chequing_id, _card_id = await _seed_two_accounts(session)

    on = date(2026, 6, 10)
    session.add(_txn(user_id, chequing_id, on, 75_00, category_id=None))
    await session.commit()

    repo = TransactionRepository(session)
    start, end = date(2026, 6, 1), date(2026, 6, 30)
    assert await repo.sum_spending(user_id, start, end) == 75_00


@pytest.mark.asyncio
async def test_bucket_stat_remaining(seeded_session):
    """target_amount and remaining, for a plain needs/wants month and for the
    savings residual, which reads in the opposite direction (a shortfall
    against goal rather than headroom left to spend)."""
    session = seeded_session
    slugs = await CategoryRepository(session).ensure_seed()
    user_id, chequing_id, _card_id = await _seed_two_accounts(session)

    on = date(2026, 6, 10)
    session.add_all(
        [
            _txn(user_id, chequing_id, on, -3200_00, slugs["salary"].id),
            _txn(user_id, chequing_id, on, 900_00, slugs["groceries"].id),
        ]
    )
    await session.commit()

    service = ReportsService(session)
    rule = await service.budget_rule(user_id, "2026-06")

    assert rule.needs.amount == 900.0
    assert rule.needs.target_amount == 1600.0
    assert rule.needs.remaining == 700.0

    assert rule.wants.amount == 0.0
    assert rule.wants.target_amount == 960.0
    assert rule.wants.remaining == 960.0

    assert rule.savings.amount == 2300.0
    assert rule.savings.target_amount == 640.0
    assert rule.savings.remaining == -1660.0
