"""Brokerage link, position sync, and live order execution against the mock broker.

The safety gates are the point of most of these tests: this is the only code
path in the app that can move real money, so "refuses to trade" is as important
to pin down as "trades correctly".
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings as app_settings

PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def _mock_market_data(monkeypatch):
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)


@pytest.fixture
def live_trading(monkeypatch):
    """Enable the env-level kill switch.

    ``live_trading_enabled`` is a computed property, so it is patched directly
    rather than via the two settings it derives from.
    """
    monkeypatch.setattr(type(app_settings), "live_trading_enabled", property(lambda self: True))


async def _register(client: AsyncClient, email: str = "broker@test.com") -> dict[str, str]:
    resp = await client.post(
        f"{PREFIX}/auth/register", json={"email": email, "password": "supersecret123"}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _link(client: AsyncClient, headers: dict[str, str]) -> list[dict]:
    start = await client.post(f"{PREFIX}/brokerage/link", headers=headers)
    assert start.status_code == 200, start.text
    assert start.json()["portal_url"]
    done = await client.post(f"{PREFIX}/brokerage/link/complete", headers=headers)
    assert done.status_code == 200, done.text
    return done.json()


async def _propose_buy(client: AsyncClient, headers: dict[str, str]) -> dict:
    """Produce a PROPOSED buy order via the rebalancer.

    The rebalancer needs an existing position to drift against, so seed a
    manual holding and then target a second symbol the cash can buy into.
    """
    for symbol, qty, cost in [("AAPL", 5, 150.0), ("VFV.TO", 1, 60.0)]:
        await client.put(
            f"{PREFIX}/invest/holdings",
            headers=headers,
            json={"symbol": symbol, "qty": qty, "avg_cost": cost},
        )
    await client.put(f"{PREFIX}/invest/cash", headers=headers, json={"cash": 5000})
    # Underweight VFV.TO against a 70% target, so the drift is a buy.
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "VFV.TO", "weight": 0.7}, {"symbol": "AAPL", "weight": 0.3}],
    )
    resp = await client.post(
        f"{PREFIX}/invest/rebalance/propose",
        headers=headers,
        json={"drift_threshold": 0.01},
    )
    assert resp.status_code == 200, resp.text
    buys = [o for o in resp.json()["orders"] if o["side"] == "buy" and o["qty"]]
    assert buys, "expected the rebalancer to propose a buy with a share quantity"
    return buys[0]


@pytest.mark.asyncio
async def test_link_passes_redirect_uri_to_the_portal(client: AsyncClient):
    """The client's deep link must reach SnapTrade as customRedirect, otherwise
    the portal's "Done" button has nowhere to send the user."""
    headers = await _register(client, "redirect@test.com")
    redirect = "aifinance://brokerage-linked"

    start = await client.post(
        f"{PREFIX}/brokerage/link", headers=headers, json={"redirect_uri": redirect}
    )
    assert start.status_code == 200, start.text
    assert f"customRedirect={redirect}" in start.json()["portal_url"]

    # Omitting it stays valid — the portal just falls back to no redirect.
    bare = await client.post(f"{PREFIX}/brokerage/link", headers=headers)
    assert bare.status_code == 200, bare.text
    assert "customRedirect" not in bare.json()["portal_url"]


@pytest.mark.asyncio
async def test_link_and_sync_positions(client: AsyncClient):
    headers = await _register(client)

    connections = await _link(client, headers)
    assert len(connections) == 1
    assert connections[0]["institution_name"] == "Wealthsimple"

    sync = await client.post(f"{PREFIX}/brokerage/sync", headers=headers)
    assert sync.status_code == 200, sync.text
    assert sync.json()["positions_synced"] == 2

    # Broker positions land as holdings, flagged so the UI can mark them
    # read-only, and drive the portfolio.
    holdings = (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json()
    assert {h["symbol"] for h in holdings} == {"VFV.TO", "XEQT.TO"}
    assert all(h["is_synced"] for h in holdings)

    # Cash is taken from the broker, not the manual field.
    portfolio = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    assert portfolio["account"]["cash"] == pytest.approx(1250.0)


@pytest.mark.asyncio
async def test_synced_holding_rejects_manual_edit(client: AsyncClient):
    headers = await _register(client, "synced@test.com")
    await _link(client, headers)
    await client.post(f"{PREFIX}/brokerage/sync", headers=headers)

    resp = await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "VFV.TO", "qty": 999, "avg_cost": 1.0},
    )
    assert resp.status_code == 422
    assert "synced" in resp.json()["detail"].lower()

    # A symbol the broker does not report is still editable by hand.
    manual = await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "AAPL", "qty": 5, "avg_cost": 150.0},
    )
    assert manual.status_code == 200, manual.text


@pytest.mark.asyncio
async def test_sync_removes_closed_positions_but_keeps_manual(client: AsyncClient):
    headers = await _register(client, "prune@test.com")
    await _link(client, headers)
    await client.post(f"{PREFIX}/brokerage/sync", headers=headers)
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "AAPL", "qty": 5, "avg_cost": 150.0},
    )

    # Unlinking drops mirrored rows only — the hand-entered one survives.
    resp = await client.delete(f"{PREFIX}/brokerage/connections", headers=headers)
    assert resp.status_code == 204

    holdings = (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json()
    assert {h["symbol"] for h in holdings} == {"AAPL"}


@pytest.mark.asyncio
async def test_order_refused_when_live_trading_disabled(client: AsyncClient):
    """The env kill switch alone blocks execution, even with a linked account."""
    headers = await _register(client, "killswitch@test.com")
    await _link(client, headers)
    order = await _propose_buy(client, headers)

    resp = await client.post(f"{PREFIX}/brokerage/orders/{order['id']}/approve", headers=headers)
    assert resp.status_code == 422
    assert "disabled" in resp.json()["detail"].lower()

    # The order is untouched and can still be executed by hand.
    orders = (await client.get(f"{PREFIX}/invest/orders/proposed", headers=headers)).json()
    assert next(o for o in orders if o["id"] == order["id"])["status"] == "proposed"


@pytest.mark.asyncio
async def test_order_refused_without_linked_account(client: AsyncClient, live_trading):
    headers = await _register(client, "nolink@test.com")
    order = await _propose_buy(client, headers)

    resp = await client.post(f"{PREFIX}/brokerage/orders/{order['id']}/approve", headers=headers)
    assert resp.status_code == 422
    assert "linked" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_approve_submits_and_fill_updates_holdings(client: AsyncClient, live_trading):
    headers = await _register(client, "execute@test.com")
    await _link(client, headers)
    order = await _propose_buy(client, headers)

    resp = await client.post(f"{PREFIX}/brokerage/orders/{order['id']}/approve", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The mock broker acknowledges first and fills on the next poll.
    assert body["status"] == "submitted"
    assert body["broker_order_id"]

    refreshed = await client.post(
        f"{PREFIX}/brokerage/orders/{order['id']}/refresh", headers=headers
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["status"] == "filled"

    holdings = (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json()
    held = next(h for h in holdings if h["symbol"] == order["symbol"])
    assert held["qty"] > 0


@pytest.mark.asyncio
async def test_order_cannot_be_approved_twice(client: AsyncClient, live_trading):
    """Guards against a double-tap in the UI placing two real orders."""
    headers = await _register(client, "double@test.com")
    await _link(client, headers)
    order = await _propose_buy(client, headers)

    first = await client.post(f"{PREFIX}/brokerage/orders/{order['id']}/approve", headers=headers)
    assert first.status_code == 200, first.text

    second = await client.post(f"{PREFIX}/brokerage/orders/{order['id']}/approve", headers=headers)
    assert second.status_code == 422
    assert "already" in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_untradable_symbol_fails_the_order_not_the_request(
    client: AsyncClient, live_trading, monkeypatch
):
    """A symbol the broker won't trade lands the order in FAILED with a reason."""
    headers = await _register(client, "untradable@test.com")
    await _link(client, headers)
    order = await _propose_buy(client, headers)

    from app.integrations.snaptrade.mock_client import MockSnapTradeClient

    async def _no_symbol(self, **kwargs):
        return None

    monkeypatch.setattr(MockSnapTradeClient, "resolve_symbol", _no_symbol)

    resp = await client.post(f"{PREFIX}/brokerage/orders/{order['id']}/approve", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "failed"
    assert "not tradable" in resp.json()["status_detail"].lower()


@pytest.mark.asyncio
async def test_user_secret_is_encrypted_at_rest(client: AsyncClient, db_session):
    """The SnapTrade secret must never sit in the DB in plaintext."""
    from sqlalchemy import select

    from app.db.models import UserSettings

    headers = await _register(client, "crypto@test.com")
    await _link(client, headers)

    row = (await db_session.execute(select(UserSettings))).scalars().first()
    assert row is not None
    assert row.snaptrade_secret_encrypted
    assert "mock-secret" not in row.snaptrade_secret_encrypted


@pytest.mark.asyncio
async def test_marking_a_trade_done_re_reads_the_broker(client: AsyncClient):
    """For a linked account the positions are the broker's, not our arithmetic.

    ``apply_fill`` moves the local numbers so the screen reacts immediately, but
    it is an estimate. Once the fill is recorded the broker is re-read, so what
    the user sees is what the brokerage account actually holds -- the two can no
    longer drift apart until someone remembers to press Sync.
    """
    headers = await _register(client, "authoritative@test.com")
    await _link(client, headers)
    await client.post(f"{PREFIX}/brokerage/sync", headers=headers)

    holdings = (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json()
    broker_qty = {h["symbol"]: h["qty"] for h in holdings}
    assert broker_qty["VFV.TO"] == 42.0  # what the mock broker reports

    accepted = await client.post(
        f"{PREFIX}/invest/actions/accept",
        headers=headers,
        json={
            "sizing": "dollars",
            "orders": [
                {
                    "symbol": "VFV.TO",
                    "side": "sell",
                    "amount": 285.0,
                    "price": 142.5,
                    "reason": "Trim the core.",
                }
            ],
        },
    )
    assert accepted.status_code == 200, accepted.text
    order = accepted.json()["orders"][0]

    resp = await client.post(
        f"{PREFIX}/invest/orders/proposed/{order['id']}/executed",
        headers=headers,
        json={"fill_price": 142.5},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "filled"

    # The broker still reports 42 shares (it has not booked the trade), so the
    # app reports 42 -- the honest answer, not the optimistic one.
    after = (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json()
    assert {h["symbol"]: h["qty"] for h in after} == broker_qty


@pytest.mark.asyncio
async def test_a_sell_larger_than_the_position_never_reaches_the_broker(
    client: AsyncClient, live_trading
):
    """The checklist is client-supplied; the broker boundary is not.

    ``apply_fill`` already refuses to book an over-sell, but it runs on
    settlement — after the order has been placed. An order that cannot be
    unplaced has to be stopped before it is sent, so this asserts the refusal
    happens at approval and that nothing was routed.
    """
    headers = await _register(client, "oversell@test.com")
    await _link(client, headers)
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "AAPL", "qty": 2, "avg_cost": 150.0},
    )

    accepted = await client.post(
        f"{PREFIX}/invest/actions/accept",
        headers=headers,
        json={
            "sizing": "shares",
            "orders": [
                {
                    "symbol": "AAPL",
                    "side": "sell",
                    "amount": 1500.0,
                    "price": 150.0,
                    "qty": 10,  # five times the position
                    "reason": "Hand-rolled payload.",
                }
            ],
        },
    )
    assert accepted.status_code == 200, accepted.text
    order = accepted.json()["orders"][0]

    resp = await client.post(f"{PREFIX}/brokerage/orders/{order['id']}/approve", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert "fewer than" in body["status_detail"]
    # Nothing was sent: a failed order carries no broker id.
    assert not body["broker_order_id"]

    # And the position is untouched.
    portfolio = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    aapl = next(p for p in portfolio["positions"] if p["symbol"] == "AAPL")
    assert aapl["qty"] == 2
