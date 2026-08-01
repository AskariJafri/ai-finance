"""Watchlist service — add/list/remove with a fake market-data provider."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.integrations.marketdata.provider import (
    BarPoint,
    MarketDataError,
    SymbolQuote,
)
from app.repositories.users import UserRepository
from app.services.errors import ValidationFailed
from app.services.watchlist_service import WatchlistService


class FakeProvider:
    """Prices anything except BADX; returns no bars (offline, deterministic)."""

    async def get_quote(self, symbol: str) -> SymbolQuote:
        if symbol.upper() == "BADX":
            raise MarketDataError("unknown symbol")
        currency = "CAD" if symbol.upper().endswith(".TO") else "USD"
        return SymbolQuote(
            symbol=symbol.upper(), price=100.0, currency=currency, name=f"{symbol.upper()} Corp"
        )

    async def get_bars(self, symbol: str, start: date, end: date) -> list[BarPoint]:
        return []


async def _make_user(session) -> uuid.UUID:
    user = await UserRepository(session).create(
        email=f"u{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    return user.id


@pytest.mark.asyncio
async def test_watchlist_add_list_remove(seeded_session):
    user_id = await _make_user(seeded_session)
    svc = WatchlistService(seeded_session, FakeProvider())

    added = await svc.add(user_id, "aapl")
    assert added.symbol == "AAPL"
    assert added.name == "AAPL Corp"
    assert added.price == 100.0
    assert added.currency == "USD"

    # A Canadian listing prices in CAD.
    ca = await svc.add(user_id, "VFV.TO")
    assert ca.currency == "CAD"

    # Adding the same symbol again is idempotent (no duplicate row).
    await svc.add(user_id, "AAPL")
    items = await svc.list(user_id)
    assert sorted(i.symbol for i in items) == ["AAPL", "VFV.TO"]

    # An unknown ticker is rejected, not silently stored.
    with pytest.raises(ValidationFailed):
        await svc.add(user_id, "BADX")

    await svc.remove(user_id, uuid.UUID(added.id))
    assert [i.symbol for i in await svc.list(user_id)] == ["VFV.TO"]


@pytest.mark.asyncio
async def test_watchlist_scoped_per_user(seeded_session):
    """One user's watchlist never leaks into another's."""
    a = await _make_user(seeded_session)
    b = await _make_user(seeded_session)
    svc = WatchlistService(seeded_session, FakeProvider())

    await svc.add(a, "MSFT")
    assert [i.symbol for i in await svc.list(a)] == ["MSFT"]
    assert await svc.list(b) == []
