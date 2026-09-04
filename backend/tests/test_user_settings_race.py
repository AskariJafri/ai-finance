"""The settings row is created lazily, so two requests can both try to make it.

`user_settings` has no row until something first needs one, which means the
creation is a read-then-insert and two concurrent requests both read nothing.
The unique index keeps the data correct; the question this pins down is what
the request that loses sees. It used to see a 500 — on a fresh account, from
an action that was perfectly valid.
"""

from __future__ import annotations

import uuid

import pytest

from app.repositories.user_settings import UserSettingsRepository
from app.repositories.users import UserRepository


async def _user(session) -> uuid.UUID:
    user = await UserRepository(session).create(
        email=f"s{uuid.uuid4().hex[:8]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    return user.id


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent(db_session):
    user_id = await _user(db_session)
    repo = UserSettingsRepository(db_session)
    first = await repo.get_or_create(user_id)
    second = await repo.get_or_create(user_id)
    assert first.id == second.id


@pytest.mark.asyncio
async def test_losing_the_insert_race_returns_the_row_the_winner_made(db_session):
    """The loser's insert hits the unique index. That is not a failure — the
    row it wanted exists, which is the whole point of get-or-create."""
    user_id = await _user(db_session)
    repo = UserSettingsRepository(db_session)
    winner = await repo.get_or_create(user_id)
    await db_session.flush()

    # Reproduce the stale read the loser performs: it looked before the winner
    # committed, so it sees nothing and goes on to insert a duplicate.
    real_get = repo.get_for_user
    seen = {"n": 0}

    async def stale(uid):
        seen["n"] += 1
        return None if seen["n"] == 1 else await real_get(uid)

    repo.get_for_user = stale  # type: ignore[method-assign]
    loser = await repo.get_or_create(user_id)

    assert seen["n"] == 2, "should have re-read after the constraint fired"
    assert loser.id == winner.id
    # The caller's transaction is still usable — the savepoint rolled back only
    # the failed insert.
    assert await repo.get_or_create(user_id) is not None
