from __future__ import annotations

import uuid

import pytest

from app.ai.categorizer import AICategorizer
from app.ai.llm.base import LLMProvider
from app.domain.enums import CategorySource
from app.repositories.categories import CategoryRepository
from app.repositories.rules import CategoryRuleRepository
from app.repositories.users import UserRepository
from app.services.categorization_service import CategorizationService


class FakeLLM(LLMProvider):
    """Deterministic LLM stub. Set ``raise_error`` to simulate an outage."""

    def __init__(self, response: str = "", raise_error: bool = False) -> None:
        self.response = response
        self.raise_error = raise_error
        self.calls = 0

    async def complete(self, prompt, *, system=None, temperature=0.0) -> str:
        self.calls += 1
        if self.raise_error:
            raise RuntimeError("ollama down")
        return self.response

    async def is_available(self) -> bool:
        return not self.raise_error


async def _make_user(session) -> uuid.UUID:
    user = await UserRepository(session).create(
        email=f"u{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    return user.id


async def _slug_of(session, category_id) -> str | None:
    if category_id is None:
        return None
    cat = await CategoryRepository(session).get_by_id(category_id)
    return cat.slug if cat else None


@pytest.mark.asyncio
async def test_seed_rule_matches_starbucks(seeded_session):
    user_id = await _make_user(seeded_session)
    svc = CategorizationService(seeded_session, AICategorizer(FakeLLM()))

    result = await svc.categorize(
        user_id=user_id, description="STARBUCKS #4821 SEATTLE", merchant_name="Starbucks"
    )
    assert result.source == CategorySource.RULE
    assert await _slug_of(seeded_session, result.category_id) == "coffee"


@pytest.mark.asyncio
async def test_ai_fallback_for_unknown_merchant(seeded_session):
    user_id = await _make_user(seeded_session)
    fake = FakeLLM(response='{"category": "restaurants", "confidence": 0.82}')
    svc = CategorizationService(seeded_session, AICategorizer(fake))

    result = await svc.categorize(
        user_id=user_id,
        description="ACME ARTISAN BAKERY",
        merchant_name="Acme Artisan Bakery",
    )
    assert fake.calls == 1
    assert result.source == CategorySource.AI
    assert result.confidence == pytest.approx(0.82)
    assert await _slug_of(seeded_session, result.category_id) == "restaurants"


@pytest.mark.asyncio
async def test_uncategorized_when_ai_unavailable(seeded_session):
    user_id = await _make_user(seeded_session)
    svc = CategorizationService(seeded_session, AICategorizer(FakeLLM(raise_error=True)))

    result = await svc.categorize(
        user_id=user_id, description="MYSTERY VENDOR XYZ", merchant_name="Mystery Vendor"
    )
    assert result.source == CategorySource.UNCATEGORIZED
    assert await _slug_of(seeded_session, result.category_id) == "uncategorized"


@pytest.mark.asyncio
async def test_credit_card_payment_categorized_as_transfer(seeded_session):
    """A card / bill payment must be a Transfer, not spending or income. The AI
    is forced offline so a miss would fall through to Uncategorized, not mask it.

    Card-specific text lands in the more specific "credit-card-payment"
    category (so the user can see *why* it left their totals); genuine
    account-to-account movement lands in the generic "transfer" category.
    Deliberately NOT tested here: broad patterns like "PRE-AUTHORIZED PAYMENT"
    or "ONLINE PAYMENT" — those were removed because they also match rent and
    utility bills (see domain/categories.py), so such inputs are an accepted
    miss for text rules alone; PFC and pair-matching catch them in practice.
    """
    user_id = await _make_user(seeded_session)
    svc = CategorizationService(seeded_session, AICategorizer(FakeLLM(raise_error=True)))

    for description, expected_slug in (
        ("AMEX EPAYMENT PAYMENT - THANK YOU", "credit-card-payment"),
        ("CREDIT CARD PAYMENT RECEIVED", "credit-card-payment"),
        ("INTERAC E-TRANSFER TO JOHN", "transfer"),
    ):
        result = await svc.categorize(user_id=user_id, description=description, merchant_name=None)
        assert result.source == CategorySource.RULE, description
        assert await _slug_of(seeded_session, result.category_id) == expected_slug, description
        assert result.is_transfer, description


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "description",
    [
        "PAYMENT - THANK YOU",
        "PAYMENT THANK YOU",
        # Canadian bilingual statements put the French translation on the line.
        "PAYMENT - THANK YOU / PAIEMENT - MERCI",
        "AMEX EPAYMENT PAYMENT - THANK YOU",
        # Trailing reference numbers are stripped by normalize_merchant.
        "PAYMENT - THANK YOU 0000123456",
        "MOBILE PAYMENT - THANK YOU",
        "CREDIT CARD PAYMENT",
    ],
)
async def test_real_world_card_payment_descriptions_are_transfers(seeded_session, description):
    """The descriptions banks actually use for a card payment. These are the
    exact strings the user reported seeing, so they're pinned here — a text-rule
    edit that stops matching any of them silently starts counting card payments
    as spending again."""
    user_id = await _make_user(seeded_session)
    svc = CategorizationService(seeded_session, AICategorizer(FakeLLM(raise_error=True)))

    result = await svc.categorize(user_id=user_id, description=description, merchant_name=None)
    assert result.is_transfer, description
    assert await _slug_of(seeded_session, result.category_id) == "credit-card-payment", description


@pytest.mark.asyncio
async def test_plaid_transfer_signal_categorized_as_transfer(seeded_session):
    """Plaid's own classification catches card payments / account transfers even
    when the description has no keyword — the robust inbound/outbound guardrail."""
    user_id = await _make_user(seeded_session)
    # AI offline so a miss would be Uncategorized, not silently masked.
    svc = CategorizationService(seeded_session, AICategorizer(FakeLLM(raise_error=True)))

    # Credit-card payment with an opaque description, flagged by Plaid's detailed
    # personal_finance_category.
    cc = await svc.categorize(
        user_id=user_id,
        description="WEB PMT 000482",
        merchant_name=None,
        plaid_category="LOAN_PAYMENTS",
        plaid_category_detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
    )
    assert cc.source == CategorySource.PLAID
    assert cc.is_transfer
    # The detailed code identifies this specifically as a card payment, so it
    # gets the more specific category rather than the generic "transfer".
    assert await _slug_of(seeded_session, cc.category_id) == "credit-card-payment"

    # A plain between-accounts transfer (primary category only, no detailed
    # code identifying it as a card payment).
    xfer = await svc.categorize(
        user_id=user_id,
        description="OUTGOING",
        merchant_name=None,
        plaid_category="TRANSFER_OUT",
    )
    assert xfer.is_transfer
    assert await _slug_of(seeded_session, xfer.category_id) == "transfer"


@pytest.mark.asyncio
async def test_user_correction_rule_overrides_seed(seeded_session):
    """A user correction becomes a high-priority rule that wins next time."""
    user_id = await _make_user(seeded_session)
    categories = CategoryRepository(seeded_session)
    rules = CategoryRuleRepository(seeded_session)

    groceries = await categories.get_system_by_slug("groceries")
    # User insists AMAZON is groceries (overriding the seed 'shopping' rule).
    await rules.upsert_user_correction(user_id=user_id, pattern="AMAZON", category_id=groceries.id)
    await seeded_session.commit()

    svc = CategorizationService(seeded_session, AICategorizer(FakeLLM()))
    result = await svc.categorize(
        user_id=user_id, description="AMAZON.COM*RT4X9", merchant_name="Amazon"
    )
    assert result.source == CategorySource.RULE
    assert result.confidence == 1.0
    assert await _slug_of(seeded_session, result.category_id) == "groceries"
