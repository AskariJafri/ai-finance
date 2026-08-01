"""Transfer-pair matching: the domain-level matcher in isolation, then the
end-to-end write path (TransactionService.match_transfers_in_range) that flags
both legs and shares a transfer_group_id."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.ai.categorizer import AICategorizer
from app.db.models import Account, PlaidItem, Transaction
from app.domain.transfer_matching import Leg, find_pairs
from app.repositories.categories import CategoryRepository
from app.repositories.users import UserRepository
from app.services.categorization_service import CategorizationService
from app.services.transaction_service import TransactionService
from tests.test_categorization import FakeLLM

ACCOUNT_A = uuid.uuid4()
ACCOUNT_B = uuid.uuid4()


def _leg(amount_cents: int, on: date, account_id=ACCOUNT_A, **kw) -> Leg:
    return Leg(
        id=uuid.uuid4(),
        account_id=account_id,
        amount_cents=amount_cents,
        date=on,
        currency="USD",
        **kw,
    )


# --- domain-level matcher ---


def test_pair_matcher_flags_both_legs():
    out = _leg(450_00, date(2026, 6, 3), account_id=ACCOUNT_A)
    inflow = _leg(-450_00, date(2026, 6, 5), account_id=ACCOUNT_B)
    pairs = find_pairs([out, inflow])
    assert pairs == [(out.id, inflow.id)]


def test_pair_matcher_ignores_same_account():
    out = _leg(100_00, date(2026, 6, 3), account_id=ACCOUNT_A)
    inflow = _leg(-100_00, date(2026, 6, 3), account_id=ACCOUNT_A)  # same account
    assert find_pairs([out, inflow]) == []


def test_pair_matcher_respects_day_gap():
    out = _leg(100_00, date(2026, 6, 1), account_id=ACCOUNT_A)
    too_far = _leg(-100_00, date(2026, 6, 10), account_id=ACCOUNT_B)  # 9 days
    assert find_pairs([out, too_far]) == []

    close_enough = _leg(-100_00, date(2026, 6, 4), account_id=ACCOUNT_B)  # 3 days
    assert find_pairs([out, close_enough], max_day_gap=3) == [(out.id, close_enough.id)]


def test_pair_matcher_skips_ambiguous_candidates():
    out = _leg(100_00, date(2026, 6, 3), account_id=ACCOUNT_A)
    candidate1 = _leg(-100_00, date(2026, 6, 4), account_id=ACCOUNT_B)
    candidate2 = _leg(-100_00, date(2026, 6, 2), account_id=ACCOUNT_B)  # same gap (1 day)
    # Never guess between equally-close candidates.
    assert find_pairs([out, candidate1, candidate2]) == []


def test_pair_matcher_respects_user_categorization():
    out = _leg(100_00, date(2026, 6, 3), account_id=ACCOUNT_A, pinned_non_transfer=True)
    inflow = _leg(-100_00, date(2026, 6, 3), account_id=ACCOUNT_B)
    assert find_pairs([out, inflow]) == []


def test_pair_matcher_is_idempotent_across_syncs():
    out = _leg(100_00, date(2026, 6, 3), account_id=ACCOUNT_A, has_group=True)
    inflow = _leg(-100_00, date(2026, 6, 3), account_id=ACCOUNT_B)
    # The outflow leg is already part of a matched pair — leave it alone.
    assert find_pairs([out, inflow]) == []


def test_pair_matcher_requires_exact_amount():
    out = _leg(100_00, date(2026, 6, 3), account_id=ACCOUNT_A)
    almost = _leg(-99_00, date(2026, 6, 3), account_id=ACCOUNT_B)
    assert find_pairs([out, almost]) == []


# --- write path: TransactionService.match_transfers_in_range ---


async def _seed_user_with_accounts(session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
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
async def test_match_transfers_in_range_flags_and_groups_both_legs(seeded_session):
    session = seeded_session
    await CategoryRepository(session).ensure_seed()
    user_id, chequing_id, card_id = await _seed_user_with_accounts(session)

    outflow = Transaction(
        user_id=user_id,
        account_id=chequing_id,
        name="PAYMENT TO VISA",
        amount_cents=450_00,
        date=date(2026, 6, 3),
    )
    inflow = Transaction(
        user_id=user_id,
        account_id=card_id,
        name="PAYMENT - THANK YOU",
        amount_cents=-450_00,
        date=date(2026, 6, 5),
    )
    session.add_all([outflow, inflow])
    await session.commit()

    categorizer = CategorizationService(session, AICategorizer(FakeLLM(raise_error=True)))
    service = TransactionService(session, categorizer)

    matched = await service.match_transfers_in_range(user_id, date(2026, 6, 1), date(2026, 6, 30))
    await session.commit()

    assert matched == 1
    await session.refresh(outflow)
    await session.refresh(inflow)
    assert outflow.is_transfer and inflow.is_transfer
    assert outflow.transfer_group_id is not None
    assert outflow.transfer_group_id == inflow.transfer_group_id

    # One leg touches a liability account, so the pair should route to the
    # more specific credit-card-payment category, not the generic transfer one.
    await session.refresh(outflow, ["category"])
    assert outflow.category is not None
    assert outflow.category.slug == "credit-card-payment"


@pytest.mark.asyncio
async def test_match_transfers_in_range_is_idempotent(seeded_session):
    session = seeded_session
    await CategoryRepository(session).ensure_seed()
    user_id, chequing_id, card_id = await _seed_user_with_accounts(session)

    outflow = Transaction(
        user_id=user_id,
        account_id=chequing_id,
        name="PAYMENT TO VISA",
        amount_cents=200_00,
        date=date(2026, 6, 3),
    )
    inflow = Transaction(
        user_id=user_id,
        account_id=card_id,
        name="PAYMENT - THANK YOU",
        amount_cents=-200_00,
        date=date(2026, 6, 3),
    )
    session.add_all([outflow, inflow])
    await session.commit()

    categorizer = CategorizationService(session, AICategorizer(FakeLLM(raise_error=True)))
    service = TransactionService(session, categorizer)

    first = await service.match_transfers_in_range(user_id, date(2026, 6, 1), date(2026, 6, 30))
    await session.commit()
    second = await service.match_transfers_in_range(user_id, date(2026, 6, 1), date(2026, 6, 30))
    await session.commit()

    assert first == 1
    assert second == 0  # already-paired legs are excluded from candidates
