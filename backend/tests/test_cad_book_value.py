"""A CAD cost basis that stays put when the exchange rate moves.

Reported from a live account: the app's gain on US holdings disagreed with the
Wealthsimple statement. The cost basis was converted at *today's* rate — the
same rate the market value used — so the currency cancelled out of the
subtraction and the app reported a USD return under a CAD label.

The test that matters is the last one in the first block: hold a US stock whose
USD price never moves, move only the exchange rate, and the CAD gain must
change. Under the old arithmetic it could not.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings as app_settings
from app.db.models import PriceBar
from app.domain.book_value import next_book_value_cad_cents
from app.integrations.marketdata.provider import BarPoint, SymbolQuote
from app.repositories.holdings import HoldingRepository
from app.repositories.users import UserRepository
from app.schemas.invest import HoldingIn
from app.services.invest_service import InvestService

USD_PRICE = 100.0


class FlatProvider:
    """NVDA sits at $100 USD forever; USDCAD is whatever `fx` says."""

    def __init__(self, fx: float = 1.30) -> None:
        self.fx = fx

    async def get_quote(self, symbol: str) -> SymbolQuote:
        currency = "CAD" if symbol.upper().endswith(".TO") else "USD"
        return SymbolQuote(symbol.upper(), USD_PRICE, currency, f"{symbol.upper()} Inc")

    async def get_bars(self, symbol: str, start: date, end: date) -> list[BarPoint]:
        days = (end - start).days
        value = self.fx if symbol.upper() == "USDCAD=X" else USD_PRICE
        return [BarPoint(start + timedelta(days=i), value) for i in range(max(days + 1, 1))]


@pytest.fixture(autouse=True)
def _mock_market_data(monkeypatch):
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)


async def _user(session) -> uuid.UUID:
    row = await UserRepository(session).create(
        email=f"fx{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    return row.id


async def _set_fx(session, rate: float) -> None:
    """Move the exchange rate without touching any share price."""
    rows = (await session.execute(select(PriceBar).where(PriceBar.symbol == "USDCAD=X"))).scalars()
    for row in rows:
        row.close = rate
    await session.commit()


# --- the reported bug ------------------------------------------------------
@pytest.mark.asyncio
async def test_a_currency_move_alone_changes_the_cad_return(seeded_session):
    user_id = await _user(seeded_session)
    svc = InvestService(seeded_session, FlatProvider(fx=1.30))
    await svc.upsert_holding(user_id, HoldingIn(symbol="NVDA", qty=10, avg_cost=USD_PRICE))

    before = await svc.portfolio(user_id)
    position = before.positions[0]
    # Typed by hand, so the rate is assumed and the row says so — but the basis
    # is locked either way, which is what this test is about.
    assert position["cost_basis_source"] == "estimated"
    # Bought at $100 with the loonie at 1.30: a $1,300 CAD book, flat on day one.
    assert position["cost_basis"] == pytest.approx(1_300.00, abs=0.01)
    assert position["unrealized_pl"] == pytest.approx(0.0, abs=0.01)

    # The share price does not move. Only the currency does.
    await _set_fx(seeded_session, 1.40)

    after = await svc.portfolio(user_id)
    position = after.positions[0]
    assert position["cost_basis"] == pytest.approx(1_300.00, abs=0.01), (
        "what it cost in CAD cannot change because the rate did"
    )
    assert position["market_value"] == pytest.approx(1_400.00, abs=0.01)
    assert position["unrealized_pl"] == pytest.approx(100.00, abs=0.01), (
        "the currency gain is real money and must show up in the CAD return"
    )


@pytest.mark.asyncio
async def test_a_cad_holding_is_untouched_by_the_rate(seeded_session):
    user_id = await _user(seeded_session)
    svc = InvestService(seeded_session, FlatProvider(fx=1.30))
    await svc.upsert_holding(user_id, HoldingIn(symbol="VFV.TO", qty=10, avg_cost=USD_PRICE))

    before = await svc.portfolio(user_id)
    await _set_fx(seeded_session, 1.90)
    after = await svc.portfolio(user_id)

    assert before.positions[0]["cost_basis"] == after.positions[0]["cost_basis"]
    assert before.positions[0]["unrealized_pl"] == after.positions[0]["unrealized_pl"]


@pytest.mark.asyncio
async def test_a_row_with_no_book_at_all_falls_back_and_says_so(seeded_session):
    """Rows that predate this bookkeeping keep the old behaviour, labelled."""
    user_id = await _user(seeded_session)
    svc = InvestService(seeded_session, FlatProvider(fx=1.30))
    await svc.upsert_holding(user_id, HoldingIn(symbol="NVDA", qty=10, avg_cost=USD_PRICE))

    repo = HoldingRepository(seeded_session)
    for row in await repo.list_for_user(user_id):
        row.book_value_cad_cents = None
    await seeded_session.commit()

    position = (await svc.portfolio(user_id)).positions[0]
    assert position["cost_basis_source"] == "estimated"
    # Falls back to converting at today's rate, which is what it did before.
    assert position["cost_basis"] == pytest.approx(1_300.00, abs=0.01)


# --- the accounting itself -------------------------------------------------
def test_buying_more_converts_only_the_new_shares():
    opened = next_book_value_cad_cents(
        previous_cad_cents=None,
        previous_native_cost_cents=0,
        native_cost_cents=10_000,
        fx_rate=1.30,
    )
    assert opened == 13_000

    added = next_book_value_cad_cents(
        previous_cad_cents=opened,
        previous_native_cost_cents=10_000,
        native_cost_cents=20_000,
        fx_rate=1.40,
    )
    # $100 bought at 1.30 plus $100 bought at 1.40 — not $200 at either rate.
    assert added == 27_000


def test_selling_shrinks_the_book_without_re_converting_it():
    book = next_book_value_cad_cents(
        previous_cad_cents=27_000,
        previous_native_cost_cents=20_000,
        native_cost_cents=10_000,
        fx_rate=1.99,  # today's rate is irrelevant to what the rest cost
    )
    assert book == 13_500


def test_closing_a_position_zeroes_the_book():
    assert (
        next_book_value_cad_cents(
            previous_cad_cents=27_000,
            previous_native_cost_cents=20_000,
            native_cost_cents=0,
            fx_rate=1.40,
        )
        == 0
    )
