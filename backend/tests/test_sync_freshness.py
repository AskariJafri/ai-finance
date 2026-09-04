"""Pressing Sync must show what the broker shows, now — not what it showed.

Two separate staleness bugs sat behind one complaint ("my portfolio value is
different from Wealthsimple"):

  * the daily-bar cache treated *today's* bar as settled, so the first sync of
    the day pinned every price until tomorrow; and
  * SnapTrade's own ``market_value`` — the number on the brokerage screen — was
    parsed on every sync and then discarded, the app preferring a figure it
    re-derived from that frozen close at its own FX rate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.core.config import settings as app_settings
from app.integrations.marketdata.provider import BarPoint
from app.repositories.holdings import HoldingRepository
from app.services.market_data_service import MarketDataService
from tests.test_brokerage import _link, _register

PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def _mock_market_data(monkeypatch):
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)


class MovingProvider:
    """One symbol whose price changes between calls, like an open market."""

    def __init__(self) -> None:
        self.price = 100.0
        self.calls = 0

    async def get_bars(self, symbol: str, start: date, end: date) -> list[BarPoint]:
        self.calls += 1
        # One bar per day across the requested range, so a range ending today
        # always includes today's.
        days = (end - start).days
        return [BarPoint(start + timedelta(days=i), self.price) for i in range(days + 1)]

    async def get_quote(self, symbol: str):  # pragma: no cover - unused here
        raise NotImplementedError


# --- the cache must not treat an open session's bar as settled -------------
@pytest.mark.asyncio
async def test_todays_bar_is_re_read_only_when_the_caller_asks(db_session):
    provider = MovingProvider()
    service = MarketDataService(db_session, provider)
    today = date.today()

    await service.sync_bars(["TEST"], since=today - timedelta(days=5))
    await db_session.commit()
    assert await service.latest_close("TEST") == 100.0

    # The market moves.
    provider.price = 107.5

    # A normal caller keeps the cache: today's bar is already there.
    await service.sync_bars(["TEST"], since=today - timedelta(days=5))
    await db_session.commit()
    assert await service.latest_close("TEST") == 100.0, (
        "an ordinary read must not re-fetch every symbol"
    )

    # The user pressed Sync.
    await service.sync_bars(["TEST"], since=today - timedelta(days=5), refresh_today=True)
    await db_session.commit()
    assert await service.latest_close("TEST") == 107.5, (
        "today's bar moves all session; a sync must re-read it"
    )


@pytest.mark.asyncio
async def test_refreshing_today_does_not_duplicate_the_bar(db_session):
    provider = MovingProvider()
    service = MarketDataService(db_session, provider)
    today = date.today()

    await service.sync_bars(["TEST"], since=today - timedelta(days=5))
    await db_session.commit()
    provider.price = 101.0
    await service.sync_bars(["TEST"], since=today - timedelta(days=5), refresh_today=True)
    await db_session.commit()

    rows = await service.bars.recent_closes("TEST", limit=5)
    dates = [d for d, _ in rows]
    assert len(dates) == len(set(dates)), "a re-read must correct the row, not add another"


# --- the portfolio must show the broker's own number ----------------------
@pytest.mark.asyncio
async def test_sync_shows_the_brokers_own_valuation(client: AsyncClient):
    headers = await _register(client, "freshsync@test.com")
    await _link(client, headers)
    assert (await client.post(f"{PREFIX}/brokerage/sync", headers=headers)).status_code == 200

    body = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    vfv = next(p for p in body["positions"] if p["symbol"] == "VFV.TO")

    # The mock broker reports 42 shares at $142.50 = $5,985 CAD.
    assert vfv["value_source"] == "broker"
    assert vfv["market_value"] == pytest.approx(5_985.00, abs=0.01)
    assert vfv["price"] == pytest.approx(142.50, abs=0.01)


@pytest.mark.asyncio
async def test_a_stale_broker_valuation_yields_to_a_newer_close(client: AsyncClient, db_session):
    """Agreement is not worth showing week-old numbers for."""
    headers = await _register(client, "stalebroker@test.com")
    await _link(client, headers)
    assert (await client.post(f"{PREFIX}/brokerage/sync", headers=headers)).status_code == 200

    # Age the broker's reading past the latest cached bar.
    repo = HoldingRepository(db_session)
    from app.repositories.users import UserRepository

    user = await UserRepository(db_session).get_by_email("stalebroker@test.com")
    for row in await repo.list_for_user(user.id):
        row.broker_valued_at = datetime.now(UTC) - timedelta(days=30)
    await db_session.commit()

    body = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    vfv = next(p for p in body["positions"] if p["symbol"] == "VFV.TO")
    assert vfv["value_source"] == "market", (
        "a month-old brokerage figure must not outrank today's close"
    )


@pytest.mark.asyncio
async def test_sync_records_a_cad_book_value(client: AsyncClient, db_session):
    """The broker gives a per-share cost, never a CAD book. We keep one."""
    headers = await _register(client, "bookvalue@test.com")
    await _link(client, headers)
    assert (await client.post(f"{PREFIX}/brokerage/sync", headers=headers)).status_code == 200

    from app.repositories.users import UserRepository

    user = await UserRepository(db_session).get_by_email("bookvalue@test.com")
    rows = {h.symbol: h for h in await HoldingRepository(db_session).list_for_user(user.id)}

    # The mock broker holds 42 VFV.TO at $119.00 CAD — a CAD listing, so the
    # book is the native cost with no conversion involved.
    assert rows["VFV.TO"].book_value_cad_cents == pytest.approx(42 * 11_900, abs=100)

    body = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    vfv = next(p for p in body["positions"] if p["symbol"] == "VFV.TO")
    assert vfv["cost_basis"] == pytest.approx(4_998.00, abs=0.01)
    # First sync of this account: every position already existed, so no rate
    # applied today is the one its shares were bought at. Locked, not exact.
    assert vfv["cost_basis_source"] == "estimated"
    assert rows["VFV.TO"].book_value_estimated is True


@pytest.mark.asyncio
async def test_a_position_opened_since_the_last_sync_has_an_exact_book(
    client: AsyncClient, db_session
):
    """Once an account has been mirrored, a new ticker is a new purchase.

    Its shares were bought at a rate the app can read, so its CAD book is a
    real book value rather than an assumption.
    """
    headers = await _register(client, "exactbook@test.com")
    # Linking already mirrors the account, so this leaves both positions known
    # and marked estimated — they existed before the app ever saw them.
    await _link(client, headers)

    from app.repositories.users import UserRepository

    user = await UserRepository(db_session).get_by_email("exactbook@test.com")
    repo = HoldingRepository(db_session)
    rows = {h.symbol: h for h in await repo.list_for_user(user.id)}
    assert rows["VFV.TO"].book_value_estimated is True

    # Drop VFV.TO so the next sync sees it appear for the first time — a
    # position opened since the account was last mirrored. XEQT.TO stays, so
    # this is plainly not a first-ever sync.
    await repo.delete(user.id, rows["VFV.TO"].id)
    await db_session.commit()

    assert (await client.post(f"{PREFIX}/brokerage/sync", headers=headers)).status_code == 200

    body = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    vfv = next(p for p in body["positions"] if p["symbol"] == "VFV.TO")
    assert vfv["cost_basis_source"] == "book"
