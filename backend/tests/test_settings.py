"""Per-user Plaid credential settings: status + set + delete, secret encrypted."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import PlaidCredential

PREFIX = "/api/v1"


async def _auth(client: AsyncClient) -> dict[str, str]:
    resp = await client.post(
        f"{PREFIX}/auth/register",
        json={"email": "keys@test.com", "password": "supersecret123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_plaid_status_defaults_to_mock(client: AsyncClient):
    headers = await _auth(client)
    resp = await client.get(f"{PREFIX}/settings/plaid", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["source"] == "mock"


@pytest.mark.asyncio
async def test_set_and_delete_plaid_credentials(client: AsyncClient):
    headers = await _auth(client)

    put = await client.put(
        f"{PREFIX}/settings/plaid",
        headers=headers,
        json={
            "client_id": "client-abc-123456",
            "secret": "super-secret-value",
            "plaid_env": "sandbox",
        },
    )
    assert put.status_code == 200, put.text
    status = put.json()
    assert status["configured"] is True
    assert status["source"] == "user"
    assert status["plaid_env"] == "sandbox"
    # Masked, last 4 shown, secret never returned.
    assert status["client_id_masked"].endswith("3456")
    assert "secret" not in status

    deleted = await client.delete(f"{PREFIX}/settings/plaid", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["source"] == "mock"


@pytest.mark.asyncio
async def test_secret_is_encrypted_at_rest(client: AsyncClient, sessionmaker):
    headers = await _auth(client)
    await client.put(
        f"{PREFIX}/settings/plaid",
        headers=headers,
        json={"client_id": "cid", "secret": "plaintext-secret", "plaid_env": "sandbox"},
    )
    async with sessionmaker() as session:  # type: AsyncSession
        row = (await session.execute(select(PlaidCredential))).scalar_one()
        assert row.secret_encrypted != "plaintext-secret"
        assert "plaintext-secret" not in row.secret_encrypted


def test_a_malformed_llm_extra_body_is_ignored_not_fatal(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("LLM_EXTRA_BODY", "{reasoning:{effort:low}}")
    assert Settings(_env_file=None).llm_extra_body == {}

    monkeypatch.setenv("LLM_EXTRA_BODY", '{"reasoning":{"effort":"low"}}')
    assert Settings(_env_file=None).llm_extra_body == {"reasoning": {"effort": "low"}}
