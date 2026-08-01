"""Net worth = cash minus card/loan debt, and the field renames it rides on."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.db.models import Account, PlaidItem
from app.repositories.users import UserRepository
from app.services.dashboard_service import DashboardService


async def _seed_user(session) -> uuid.UUID:
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
    return user.id, item.id


def _account(user_id, item_id, *, type_: str | None, balance_cents: int) -> Account:
    return Account(
        user_id=user_id,
        plaid_item_id=item_id,
        plaid_account_id=f"acc-{uuid.uuid4().hex[:8]}",
        name="Account",
        type=type_,
        current_balance_cents=balance_cents,
    )


@pytest.mark.asyncio
async def test_total_balance_is_net_worth(seeded_session):
    """Net worth = cash (depository + NULL-type) minus card/loan debt.
    Deliberately NOT "every non-liability account": an investment account
    would double-count against the Invest tab, which tracks holdings
    separately."""
    session = seeded_session
    user_id, item_id = await _seed_user(session)
    session.add_all(
        [
            _account(user_id, item_id, type_="depository", balance_cents=100_000_00),
            _account(user_id, item_id, type_="credit", balance_cents=30_000_00),
            _account(user_id, item_id, type_="loan", balance_cents=500_000_00),
            _account(user_id, item_id, type_=None, balance_cents=5_000_00),
            # An investment account must not be netted into this banking figure.
            _account(user_id, item_id, type_="investment", balance_cents=999_999_00),
        ]
    )
    await session.commit()

    summary = await DashboardService(session).summary(user_id)
    assert summary.cash_balance == 100_000.00 + 5_000.00
    assert summary.credit_balance == 30_000.00 + 500_000.00
    assert summary.total_balance == (100_000.00 + 5_000.00) - (30_000.00 + 500_000.00)


@pytest.mark.asyncio
async def test_overpaid_card_adds_to_net_worth(seeded_session):
    """Plaid reports an overpaid card's balance as negative — subtracting a
    negative correctly adds it back. No clamping."""
    session = seeded_session
    user_id, item_id = await _seed_user(session)
    session.add_all(
        [
            _account(user_id, item_id, type_="depository", balance_cents=10_000_00),
            _account(user_id, item_id, type_="credit", balance_cents=-500_00),
        ]
    )
    await session.commit()

    summary = await DashboardService(session).summary(user_id)
    assert summary.credit_balance == -500.00
    assert summary.total_balance == 10_000.00 - (-500.00)


@pytest.mark.asyncio
async def test_period_label_present(seeded_session):
    session = seeded_session
    user_id, _item_id = await _seed_user(session)
    summary = await DashboardService(session).summary(user_id)
    assert summary.period_label == "Last 30 days"
    assert summary.period_start <= summary.period_end == date.today().isoformat()
