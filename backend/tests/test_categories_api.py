"""Custom category CRUD — the /categories router and CategoryService rules."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.ai.categorizer import AICategorizer
from app.ai.prompts import build_categorize_prompt
from app.domain.enums import CategorySource
from app.services.categorization_service import CategorizationService
from tests.test_categorization import FakeLLM, _make_user

PREFIX = "/api/v1"


async def _register(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post(
        f"{PREFIX}/auth/register",
        json={"email": email, "password": "supersecret123"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_create_custom_category(client: AsyncClient):
    headers = await _register(client, "cat1@test.com")
    resp = await client.post(
        f"{PREFIX}/categories",
        headers=headers,
        json={"name": "Home Gym", "group": "expense", "budget_bucket": "wants"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_system"] is False
    assert body["slug"] == "home-gym"
    assert body["budget_bucket"] == "wants"


@pytest.mark.asyncio
async def test_create_slug_colliding_with_system_is_uniquified(client: AsyncClient, sessionmaker):
    headers = await _register(client, "cat2@test.com")
    resp = await client.post(
        f"{PREFIX}/categories",
        headers=headers,
        json={"name": "Groceries", "group": "expense"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["slug"] == "groceries-2"

    me = await client.get(f"{PREFIX}/auth/me", headers=headers)
    user_id = uuid.UUID(me.json()["id"])

    # The shadowing guard: the categorizer's internal slug map must still
    # resolve "groceries" to the SYSTEM category, never the new user row.
    async with sessionmaker() as session:
        svc = CategorizationService(session, AICategorizer(FakeLLM(raise_error=True)))
        await svc._load(user_id)
        assert svc._by_slug["groceries"].is_system is True


@pytest.mark.asyncio
async def test_create_transfer_group_rejected(client: AsyncClient):
    headers = await _register(client, "cat3@test.com")
    resp = await client.post(
        f"{PREFIX}/categories",
        headers=headers,
        json={"name": "My Transfers", "group": "transfer"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_invalid_color_rejected(client: AsyncClient):
    headers = await _register(client, "cat4@test.com")
    resp = await client.post(
        f"{PREFIX}/categories",
        headers=headers,
        json={"name": "Bad Color", "group": "expense", "color": "not-a-color"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_slug_or_group_rejected(client: AsyncClient):
    headers = await _register(client, "cat5@test.com")
    created = await client.post(
        f"{PREFIX}/categories", headers=headers, json={"name": "Hobbies", "group": "expense"}
    )
    category_id = created.json()["id"]

    resp = await client.patch(
        f"{PREFIX}/categories/{category_id}", headers=headers, json={"slug": "hacked"}
    )
    assert resp.status_code == 422

    resp = await client.patch(
        f"{PREFIX}/categories/{category_id}", headers=headers, json={"group": "income"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_name_color_bucket(client: AsyncClient):
    headers = await _register(client, "cat6@test.com")
    created = await client.post(
        f"{PREFIX}/categories",
        headers=headers,
        json={"name": "Hobbies", "group": "expense", "budget_bucket": "wants"},
    )
    category_id = created.json()["id"]

    resp = await client.patch(
        f"{PREFIX}/categories/{category_id}",
        headers=headers,
        json={"name": "Hobbies & Crafts", "color": "#123456", "budget_bucket": "needs"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Hobbies & Crafts"
    assert body["color"] == "#123456"
    assert body["budget_bucket"] == "needs"
    assert body["slug"] == "hobbies"  # unchanged


@pytest.mark.asyncio
async def test_delete_reassigns_to_uncategorized_and_sets_source(client: AsyncClient):
    headers = await _register(client, "cat7@test.com")
    await client.post(
        f"{PREFIX}/plaid/link/exchange",
        headers=headers,
        json={"public_token": "public-sandbox-abc"},
    )

    before = (await client.get(f"{PREFIX}/dashboard/summary", headers=headers)).json()
    before_uncategorized = before["uncategorized_count"]

    created = await client.post(
        f"{PREFIX}/categories", headers=headers, json={"name": "Temp Category", "group": "expense"}
    )
    category_id = created.json()["id"]

    txns = (await client.get(f"{PREFIX}/transactions", headers=headers)).json()
    target = txns[0]
    # Whether this transaction starts out uncategorized depends on how much of
    # the categorizer ran: rules always apply, but the LLM leg is skipped when
    # no provider is reachable (as in CI). Moving an *already* uncategorized
    # transaction into Temp Category drops the count by one first, so the net
    # change after deleting the category is zero rather than plus one. Capture
    # the starting state instead of assuming it.
    target_was_uncategorized = target["category"]["slug"] == "uncategorized"
    patched = await client.patch(
        f"{PREFIX}/transactions/{target['id']}/category",
        headers=headers,
        json={"category_id": category_id},
    )
    assert patched.status_code == 200, patched.text

    delete_resp = await client.delete(f"{PREFIX}/categories/{category_id}", headers=headers)
    assert delete_resp.status_code == 204

    refreshed = await client.get(f"{PREFIX}/transactions", headers=headers)
    moved = next(t for t in refreshed.json() if t["id"] == target["id"])
    assert moved["category"]["slug"] == "uncategorized"
    assert moved["category_source"] == CategorySource.UNCATEGORIZED.value

    after = (await client.get(f"{PREFIX}/dashboard/summary", headers=headers)).json()
    expected = before_uncategorized + (0 if target_was_uncategorized else 1)
    assert after["uncategorized_count"] == expected


@pytest.mark.asyncio
async def test_delete_system_category_rejected(client: AsyncClient):
    headers = await _register(client, "cat8@test.com")
    categories = (await client.get(f"{PREFIX}/categories", headers=headers)).json()
    groceries = next(c for c in categories if c["slug"] == "groceries")

    resp = await client.delete(f"{PREFIX}/categories/{groceries['id']}", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cannot_edit_or_delete_another_users_category(client: AsyncClient):
    a_headers = await _register(client, "cat9a@test.com")
    b_headers = await _register(client, "cat9b@test.com")

    created = await client.post(
        f"{PREFIX}/categories", headers=a_headers, json={"name": "A's Category", "group": "expense"}
    )
    category_id = created.json()["id"]

    edit = await client.patch(
        f"{PREFIX}/categories/{category_id}", headers=b_headers, json={"name": "Hijacked"}
    )
    assert edit.status_code == 404

    delete_resp = await client.delete(f"{PREFIX}/categories/{category_id}", headers=b_headers)
    assert delete_resp.status_code == 404


@pytest.mark.asyncio
async def test_recategorize_with_foreign_category_id_rejected(client: AsyncClient):
    a_headers = await _register(client, "cat10a@test.com")
    b_headers = await _register(client, "cat10b@test.com")

    await client.post(
        f"{PREFIX}/plaid/link/exchange",
        headers=a_headers,
        json={"public_token": "public-sandbox-abc"},
    )
    b_category = await client.post(
        f"{PREFIX}/categories", headers=b_headers, json={"name": "B's Category", "group": "expense"}
    )
    b_category_id = b_category.json()["id"]

    a_txns = (await client.get(f"{PREFIX}/transactions", headers=a_headers)).json()
    resp = await client.patch(
        f"{PREFIX}/transactions/{a_txns[0]['id']}/category",
        headers=a_headers,
        json={"category_id": b_category_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_custom_needs_category_lands_in_needs_bucket(client: AsyncClient):
    headers = await _register(client, "cat11@test.com")
    await client.post(
        f"{PREFIX}/plaid/link/exchange",
        headers=headers,
        json={"public_token": "public-sandbox-abc"},
    )

    created = await client.post(
        f"{PREFIX}/categories",
        headers=headers,
        json={"name": "Childcare", "group": "expense", "budget_bucket": "needs"},
    )
    category_id = created.json()["id"]

    txns = (await client.get(f"{PREFIX}/transactions", headers=headers)).json()
    target = next(t for t in txns if t["merchant_name"] == "Starbucks")
    await client.patch(
        f"{PREFIX}/transactions/{target['id']}/category",
        headers=headers,
        json={"category_id": category_id},
    )

    rule = (await client.get(f"{PREFIX}/reports/budget-rule", headers=headers)).json()
    # Starbucks (6.75) moved out of wants and into needs.
    assert rule["needs"]["amount"] >= 6.75


@pytest.mark.asyncio
async def test_custom_slug_offered_to_ai_with_display_name(seeded_session):
    from app.domain.enums import CategoryGroup
    from app.repositories.categories import CategoryRepository

    user_id = await _make_user(seeded_session)
    await CategoryRepository(seeded_session).ensure_seed()
    await CategoryRepository(seeded_session).create(
        user_id=user_id,
        name="Home Groceries",
        slug="groceries-2",
        group=CategoryGroup.EXPENSE,
        color=None,
        budget_bucket="needs",
    )
    await seeded_session.commit()

    captured: dict[str, str] = {}

    class CapturingLLM(FakeLLM):
        async def complete(self, prompt, *, system=None, temperature=0.0) -> str:
            captured["prompt"] = prompt
            return '{"category": "uncategorized", "confidence": 0.1}'

    svc = CategorizationService(seeded_session, AICategorizer(CapturingLLM()))
    await svc.categorize(
        user_id=user_id, description="MYSTERY VENDOR", merchant_name="Mystery Vendor"
    )

    assert "groceries-2 (Home Groceries)" in captured["prompt"]


def test_prompt_renders_display_names():
    prompt = build_categorize_prompt(
        description="x",
        merchant_name=None,
        category_slugs=["groceries-2"],
        category_names=[("groceries-2", "Home Groceries")],
    )
    assert "groceries-2 (Home Groceries)" in prompt
