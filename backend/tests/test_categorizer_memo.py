"""One metered request per *merchant*, not per transaction.

The AI branch of categorization is the highest-volume LLM caller in the app and
the only one driven by row count rather than user action. A sync of sixty rows
used to be sixty requests against an allowance of roughly fifty a day, which is
what made every other AI surface look "offline" by lunchtime.

These pin the memo that fixes it, including the case that is easy to forget:
the merchant the model *cannot* place.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.ai.categorizer import AICategorizer
from app.repositories.users import UserRepository
from app.services.categorization_service import CategorizationService
from tests.test_categorization import FakeLLM


def _reply(slug: str, confidence: float = 0.9) -> str:
    return json.dumps({"category": slug, "confidence": confidence})


async def _user(session) -> uuid.UUID:
    user = await UserRepository(session).create(
        email=f"memo{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    return user.id


def _service(session, llm) -> CategorizationService:
    return CategorizationService(session, AICategorizer(llm))


async def _categorize(svc, user_id, description, merchant=None):
    return await svc.categorize(user_id=user_id, description=description, merchant_name=merchant)


@pytest.mark.asyncio
async def test_the_same_merchant_twice_costs_one_request(seeded_session):
    llm = FakeLLM(_reply("groceries"))
    svc = _service(seeded_session, llm)
    user_id = await _user(seeded_session)

    first = await _categorize(svc, user_id, "PURCHASE 8817", "Zzyzx Provisions")
    second = await _categorize(svc, user_id, "PURCHASE 9241", "Zzyzx Provisions")

    assert llm.calls == 1, "the second row must reuse the first row's answer"
    assert first.category_id == second.category_id
    assert first.confidence == second.confidence


@pytest.mark.asyncio
async def test_different_merchants_each_cost_a_request(seeded_session):
    """The memo must not collapse genuinely different payees."""
    llm = FakeLLM(_reply("groceries"))
    svc = _service(seeded_session, llm)
    user_id = await _user(seeded_session)

    await _categorize(svc, user_id, "PURCHASE 1", "Zzyzx Provisions")
    await _categorize(svc, user_id, "PURCHASE 2", "Qwertyuiop Hardware")

    assert llm.calls == 2


@pytest.mark.asyncio
async def test_a_merchant_the_model_cannot_place_is_only_asked_once(seeded_session):
    """The miss is memoized too.

    Without this, the one payee the model has no answer for is re-asked on
    every row it appears in — turning the best case into the worst one.
    """
    llm = FakeLLM(_reply("not-a-real-slug"))  # rejected by the slug allow-list
    svc = _service(seeded_session, llm)
    user_id = await _user(seeded_session)

    for _ in range(4):
        await _categorize(svc, user_id, "PURCHASE", "Unplaceable Payee Ltd")

    assert llm.calls == 1


@pytest.mark.asyncio
async def test_a_merchantless_row_memoizes_on_its_description(seeded_session):
    """Plaid often sends no merchant_name; the description is the only key."""
    llm = FakeLLM(_reply("groceries"))
    svc = _service(seeded_session, llm)
    user_id = await _user(seeded_session)

    await _categorize(svc, user_id, "ZZYZX PROVISIONS 8817", None)
    await _categorize(svc, user_id, "ZZYZX PROVISIONS 8817", None)

    assert llm.calls == 1


@pytest.mark.asyncio
async def test_the_memo_does_not_outlive_the_request(seeded_session):
    """It is request-scoped, not a cache of AI output across syncs.

    The service is built per request, so a fresh instance asks again — which is
    what keeps this from being the caching the user ruled out.
    """
    llm = FakeLLM(_reply("groceries"))
    user_id = await _user(seeded_session)

    await _categorize(_service(seeded_session, llm), user_id, "PURCHASE", "Zzyzx")
    await _categorize(_service(seeded_session, llm), user_id, "PURCHASE", "Zzyzx")

    assert llm.calls == 2


@pytest.mark.asyncio
async def test_invalidate_clears_the_memo(seeded_session):
    """The answer is a slug from the category set that just changed."""
    llm = FakeLLM(_reply("groceries"))
    svc = _service(seeded_session, llm)
    user_id = await _user(seeded_session)

    await _categorize(svc, user_id, "PURCHASE", "Zzyzx")
    svc.invalidate()
    await _categorize(svc, user_id, "PURCHASE", "Zzyzx")

    assert llm.calls == 2
