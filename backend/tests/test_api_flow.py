"""End-to-end API flow against the ASGI app with the mock Plaid client.

Covers the Phase 1 walking skeleton: register -> auth -> link token -> exchange
(accounts + transactions synced + categorized) -> dashboard -> list -> correct
category (learning loop records a user category).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

PREFIX = "/api/v1"


async def _register(client: AsyncClient) -> dict[str, str]:
    resp = await client.post(
        f"{PREFIX}/auth/register",
        json={"email": "sam@test.com", "password": "supersecret123", "full_name": "Sam"},
    )
    assert resp.status_code == 201, resp.text
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_auth_register_login_me_refresh(client: AsyncClient):
    headers = await _register(client)

    me = await client.get(f"{PREFIX}/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "sam@test.com"

    login = await client.post(
        f"{PREFIX}/auth/login",
        json={"email": "sam@test.com", "password": "supersecret123"},
    )
    assert login.status_code == 200
    refresh_token = login.json()["refresh_token"]

    refreshed = await client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    assert "access_token" in refreshed.json()


@pytest.mark.asyncio
async def test_duplicate_registration_conflicts(client: AsyncClient):
    await _register(client)
    resp = await client.post(
        f"{PREFIX}/auth/register",
        json={"email": "sam@test.com", "password": "supersecret123"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_me_requires_auth(client: AsyncClient):
    resp = await client.get(f"{PREFIX}/auth/me")
    assert resp.status_code == 401  # no bearer credentials


@pytest.mark.asyncio
async def test_plaid_link_exchange_sync_and_dashboard(client: AsyncClient):
    headers = await _register(client)

    link = await client.post(f"{PREFIX}/plaid/link/token", headers=headers)
    assert link.status_code == 200
    assert link.json()["link_token"].startswith("link-sandbox-mock-")

    exchange = await client.post(
        f"{PREFIX}/plaid/link/exchange",
        headers=headers,
        json={"public_token": "public-sandbox-abc"},
    )
    assert exchange.status_code == 200, exchange.text
    body = exchange.json()
    assert body["accounts_synced"] == 2
    assert body["transactions_synced"] == 14

    # Accounts persisted.
    accounts = await client.get(f"{PREFIX}/accounts", headers=headers)
    assert accounts.status_code == 200
    assert len(accounts.json()) == 2

    # Dashboard aggregates come purely from stored data.
    dash = await client.get(f"{PREFIX}/dashboard/summary", headers=headers)
    assert dash.status_code == 200
    summary = dash.json()
    # The credit-card payment (a -450 inflow on the card) must NOT be counted as
    # income — earnings stay at the 3200 payroll, not 3650. And its outflow leg
    # (+450 on checking) must NOT be counted as spending either — an exact
    # figure here, not just "> 0", because a regression that mirrors the
    # liability guard onto spending or drops the transfer flag would silently
    # change this number rather than fail loudly.
    assert summary["period_income"] == 3200.0
    assert summary["period_spending"] == 514.40
    # Net worth = cash ($8,423.55) minus card debt ($1,234.56).
    assert summary["total_balance"] == 7188.99
    assert summary["cash_balance"] == 8423.55
    assert summary["credit_balance"] == 1234.56
    assert len(summary["spend_by_category"]) >= 1
    assert len(summary["recent_transactions"]) > 0

    # A second sync is idempotent (cursor exhausted -> nothing new).
    resync = await client.post(f"{PREFIX}/plaid/sync", headers=headers)
    assert resync.status_code == 200
    assert resync.json()["added"] == 0


@pytest.mark.asyncio
async def test_relink_same_institution_replaces_old_item(client: AsyncClient):
    headers = await _register(client)

    for _ in range(2):
        resp = await client.post(
            f"{PREFIX}/plaid/link/exchange",
            headers=headers,
            json={"public_token": "public-sandbox-abc"},
        )
        assert resp.status_code == 200, resp.text

    # Relinking must replace the previous connection, not stack a duplicate —
    # otherwise every balance and transaction is double-counted.
    accounts = (await client.get(f"{PREFIX}/accounts", headers=headers)).json()
    assert len(accounts) == 2

    txns = (await client.get(f"{PREFIX}/transactions", headers=headers)).json()
    assert len(txns) == 14


@pytest.mark.asyncio
async def test_category_correction_learning_loop(client: AsyncClient):
    headers = await _register(client)
    await client.post(
        f"{PREFIX}/plaid/link/exchange",
        headers=headers,
        json={"public_token": "public-sandbox-abc"},
    )

    txns = (await client.get(f"{PREFIX}/transactions", headers=headers)).json()
    assert len(txns) == 14

    categories = (await client.get(f"{PREFIX}/transactions/categories", headers=headers)).json()
    groceries = next(c for c in categories if c["slug"] == "groceries")

    # Correct the unknown (AI/uncategorized) bakery transaction to groceries.
    target = next(t for t in txns if "ACME" in t["name"])
    patched = await client.patch(
        f"{PREFIX}/transactions/{target['id']}/category",
        headers=headers,
        json={"category_id": groceries["id"]},
    )
    assert patched.status_code == 200, patched.text
    updated = patched.json()
    assert updated["category"]["slug"] == "groceries"
    assert updated["category_source"] == "user"

    # The correction sticks on subsequent reads.
    txns_after = (await client.get(f"{PREFIX}/transactions", headers=headers)).json()
    corrected = next(t for t in txns_after if t["id"] == target["id"])
    assert corrected["category"]["slug"] == "groceries"
    assert corrected["category_source"] == "user"
