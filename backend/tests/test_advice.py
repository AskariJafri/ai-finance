"""Advice service: caching semantics, LLM failure fallback, disclaimer."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.ai.llm.base import LLMProvider, LLMUnavailable
from app.db.models import Account, PlaidItem, Transaction
from app.repositories.categories import CategoryRepository
from app.repositories.users import UserRepository
from app.services.advice_service import DISCLAIMER, AdviceService

MONTH = "2026-06"


class FakeLLM(LLMProvider):
    def __init__(self, response: str = "Solid month — keep it up.") -> None:
        self.response = response
        self.calls = 0
        self.model = "fake-model"

    async def complete(self, prompt, *, system=None, temperature=0.0, json_object=False) -> str:
        self.calls += 1
        return self.response

    async def is_available(self) -> bool:
        return True


class FailingLLM(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, prompt, *, system=None, temperature=0.0, json_object=False) -> str:
        self.calls += 1
        raise LLMUnavailable("model unreachable")

    async def is_available(self) -> bool:
        return False


async def _seed_month_of_data(session) -> uuid.UUID:
    user = await UserRepository(session).create(
        email=f"u{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    item = PlaidItem(
        user_id=user.id,
        plaid_item_id=f"i-{uuid.uuid4().hex[:8]}",
        access_token_encrypted="enc",
    )
    session.add(item)
    await session.flush()
    account = Account(
        user_id=user.id,
        plaid_item_id=item.id,
        plaid_account_id=f"a-{uuid.uuid4().hex[:8]}",
        name="Chequing",
    )
    session.add(account)
    await session.flush()
    slugs = await CategoryRepository(session).ensure_seed()
    session.add_all(
        [
            Transaction(
                user_id=user.id,
                account_id=account.id,
                name="pay",
                amount_cents=-3000_00,
                date=date(2026, 6, 1),
                category_id=slugs["salary"].id,
            ),
            Transaction(
                user_id=user.id,
                account_id=account.id,
                name="rent",
                amount_cents=1500_00,
                date=date(2026, 6, 2),
                category_id=slugs["rent"].id,
            ),
        ]
    )
    await session.commit()
    return user.id


@pytest.mark.asyncio
async def test_generate_caches_and_reuses(seeded_session):
    user_id = await _seed_month_of_data(seeded_session)
    fake = FakeLLM()
    svc = AdviceService(seeded_session, fake)

    # Nothing cached before the first generate.
    out = await svc.get(user_id, MONTH)
    assert out.source == "none"
    assert out.content is None
    assert out.disclaimer == DISCLAIMER
    assert fake.calls == 0

    out = await svc.generate(user_id, MONTH)
    assert out.source == "ai"
    assert out.content == "Solid month — keep it up."
    assert out.model == "fake-model"
    assert out.generated_at is not None
    assert fake.calls == 1

    # Second generate returns the cache without calling the LLM…
    out = await svc.generate(user_id, MONTH)
    assert out.source == "ai"
    assert fake.calls == 1
    # …and so does get().
    out = await svc.get(user_id, MONTH)
    assert out.source == "ai"

    # force=True regenerates.
    fake.response = "New take."
    out = await svc.generate(user_id, MONTH, force=True)
    assert out.content == "New take."
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_llm_failure_falls_back_and_never_caches(seeded_session):
    user_id = await _seed_month_of_data(seeded_session)
    failing = FailingLLM()
    svc = AdviceService(seeded_session, failing)

    out = await svc.generate(user_id, MONTH)
    assert out.source == "fallback"
    assert out.content  # non-empty deterministic tips
    assert "rent" in out.content.lower() or "Rent" in out.content
    assert out.disclaimer == DISCLAIMER

    # Fallback is never cached, so the AI is retried next time.
    assert (await svc.get(user_id, MONTH)).source == "none"
    await svc.generate(user_id, MONTH)
    assert failing.calls == 2


@pytest.mark.asyncio
async def test_ai_disabled_uses_fallback_without_calling_llm(seeded_session, monkeypatch):
    user_id = await _seed_month_of_data(seeded_session)
    fake = FakeLLM()
    svc = AdviceService(seeded_session, fake)
    monkeypatch.setattr("app.services.advice_service.settings.ai_enabled", False)

    out = await svc.generate(user_id, MONTH)
    assert out.source == "fallback"
    assert fake.calls == 0
