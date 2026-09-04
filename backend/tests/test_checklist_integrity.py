"""The trade checklist must not lose orders, and a fill must move real shares.

Two regressions this covers, both of which end with the app disagreeing with
the brokerage account it is mirroring:

* asking for a rebalance that has nothing to suggest used to expire every open
  order anyway, so a buy the user was still working through just vanished;
* a dollar-sized order carries its amount in ``notional_cents`` and leaves
  ``qty`` NULL, and marking one done used to move zero shares while moving the
  full cash amount.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings as app_settings

PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def _mock_market_data(monkeypatch):
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)


async def _register(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post(
        f"{PREFIX}/auth/register", json={"email": email, "password": "supersecret123"}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _open_orders(client: AsyncClient, headers: dict[str, str]) -> list[dict]:
    resp = await client.get(f"{PREFIX}/invest/orders/proposed?status=proposed", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_a_rebalance_with_nothing_to_suggest_keeps_the_open_checklist(
    client: AsyncClient,
):
    """Asking a question must not cost the user the orders they already have."""
    headers = await _register(client, "keepchecklist@test.com")
    for symbol in ("VFV.TO", "XEQT.TO"):
        await client.put(
            f"{PREFIX}/invest/holdings",
            headers=headers,
            json={"symbol": symbol, "qty": 10, "avg_cost": 100.0},
        )
    await client.put(f"{PREFIX}/invest/cash", headers=headers, json={"cash": 0})

    # A checklist the user is part-way through.
    accepted = await client.post(
        f"{PREFIX}/invest/actions/accept",
        headers=headers,
        json={
            "sizing": "dollars",
            "orders": [
                {
                    "symbol": "MFC.TO",
                    "side": "buy",
                    "amount": 40.72,
                    "price": 58.6,
                    "reason": "Add insurance exposure.",
                }
            ],
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert [o["symbol"] for o in await _open_orders(client, headers)] == ["MFC.TO"]

    # Targets the portfolio already satisfies, so the rebalancer has nothing
    # to propose.
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "VFV.TO", "weight": 0.5}, {"symbol": "XEQT.TO", "weight": 0.5}],
    )
    proposal = await client.post(
        f"{PREFIX}/invest/rebalance/propose",
        headers=headers,
        json={"drift_threshold": 0.5, "sizing": "shares"},
    )
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["orders"] == [], "expected an empty plan for this setup"

    # The open order survives: nothing replaced it, so nothing superseded it.
    assert [o["symbol"] for o in await _open_orders(client, headers)] == ["MFC.TO"]


@pytest.mark.asyncio
async def test_a_rebalance_with_orders_still_supersedes_the_old_batch(client: AsyncClient):
    """The guard must not break the case it was guarding."""
    headers = await _register(client, "supersede@test.com")
    for symbol in ("VFV.TO", "XEQT.TO"):
        await client.put(
            f"{PREFIX}/invest/holdings",
            headers=headers,
            json={"symbol": symbol, "qty": 10, "avg_cost": 100.0},
        )
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        # Explicit 0%, so XEQT is sold out rather than left alone as unmanaged.
        json=[{"symbol": "VFV.TO", "weight": 1.0}, {"symbol": "XEQT.TO", "weight": 0.0}],
    )

    first = await client.post(
        f"{PREFIX}/invest/rebalance/propose",
        headers=headers,
        json={"drift_threshold": 0.01, "sizing": "shares"},
    )
    assert first.json()["orders"], "expected a plan"
    first_ids = {o["id"] for o in first.json()["orders"]}

    second = await client.post(
        f"{PREFIX}/invest/rebalance/propose",
        headers=headers,
        json={"drift_threshold": 0.01, "sizing": "shares"},
    )
    assert second.json()["orders"], "expected a plan"

    open_ids = {o["id"] for o in await _open_orders(client, headers)}
    assert not (first_ids & open_ids), "the older batch should have been superseded"


@pytest.mark.asyncio
async def test_filling_a_dollar_sized_sell_moves_shares_not_just_cash(client: AsyncClient):
    """A notional order has no qty of its own - derive it from the amount."""
    headers = await _register(client, "notionalsell@test.com")
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "ZAG.TO", "qty": 10, "avg_cost": 13.0},
    )
    await client.put(f"{PREFIX}/invest/cash", headers=headers, json={"cash": 100})

    before = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    price = next(p["price"] for p in before["positions"] if p["symbol"] == "ZAG.TO")
    amount = round(price * 2, 2)  # two shares' worth, expressed in dollars

    accepted = await client.post(
        f"{PREFIX}/invest/actions/accept",
        headers=headers,
        json={
            "sizing": "dollars",
            "orders": [
                {
                    "symbol": "ZAG.TO",
                    "side": "sell",
                    "amount": amount,
                    "price": price,
                    "reason": "Trim bonds.",
                }
            ],
        },
    )
    order = accepted.json()["orders"][0]
    assert order["qty"] is None, "a dollar-sized order carries no share count"

    resp = await client.post(
        f"{PREFIX}/invest/orders/proposed/{order['id']}/executed",
        headers=headers,
        json={"fill_price": price},
    )
    assert resp.status_code == 200, resp.text

    after = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    position = next(p for p in after["positions"] if p["symbol"] == "ZAG.TO")
    assert position["qty"] == pytest.approx(8, abs=0.01), "the shares must actually leave"
    assert after["account"]["cash"] == pytest.approx(before["account"]["cash"] + amount, abs=0.05)
    # The trade is an exchange: the total is conserved.
    assert after["account"]["equity"] == pytest.approx(before["account"]["equity"], abs=0.05)


@pytest.mark.asyncio
async def test_filling_a_dollar_sized_buy_adds_shares(client: AsyncClient):
    headers = await _register(client, "notionalbuy@test.com")
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "VFV.TO", "qty": 5, "avg_cost": 100.0},
    )
    await client.put(f"{PREFIX}/invest/cash", headers=headers, json={"cash": 5000})

    before = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    price = next(p["price"] for p in before["positions"] if p["symbol"] == "VFV.TO")
    amount = round(price * 3, 2)

    accepted = await client.post(
        f"{PREFIX}/invest/actions/accept",
        headers=headers,
        json={
            "sizing": "dollars",
            "orders": [
                {
                    "symbol": "VFV.TO",
                    "side": "buy",
                    "amount": amount,
                    "price": price,
                    "reason": "Add to the core.",
                }
            ],
        },
    )
    order = accepted.json()["orders"][0]
    resp = await client.post(
        f"{PREFIX}/invest/orders/proposed/{order['id']}/executed",
        headers=headers,
        json={"fill_price": price},
    )
    assert resp.status_code == 200, resp.text

    after = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    position = next(p for p in after["positions"] if p["symbol"] == "VFV.TO")
    assert position["qty"] == pytest.approx(8, abs=0.01)
    assert after["account"]["cash"] == pytest.approx(before["account"]["cash"] - amount, abs=0.05)
    assert after["account"]["equity"] == pytest.approx(before["account"]["equity"], abs=0.05)


@pytest.mark.asyncio
async def test_a_dollar_sell_larger_than_the_position_closes_it_out(client: AsyncClient):
    """Selling more dollars than are held is a full exit, not an over-sell."""
    headers = await _register(client, "closeout@test.com")
    for symbol, qty in (("ZAG.TO", 2), ("VFV.TO", 1)):
        await client.put(
            f"{PREFIX}/invest/holdings",
            headers=headers,
            json={"symbol": symbol, "qty": qty, "avg_cost": 13.0},
        )

    before = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    price = next(p["price"] for p in before["positions"] if p["symbol"] == "ZAG.TO")

    accepted = await client.post(
        f"{PREFIX}/invest/actions/accept",
        headers=headers,
        json={
            "sizing": "dollars",
            "orders": [
                {
                    "symbol": "ZAG.TO",
                    "side": "sell",
                    "amount": round(price * 5, 2),  # more than is held
                    "price": price,
                    "reason": "Exit bonds.",
                }
            ],
        },
    )
    order = accepted.json()["orders"][0]
    resp = await client.post(
        f"{PREFIX}/invest/orders/proposed/{order['id']}/executed",
        headers=headers,
        json={"fill_price": price},
    )
    assert resp.status_code == 200, resp.text

    after = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    assert all(p["symbol"] != "ZAG.TO" for p in after["positions"]), "position should be closed"
    # Only what was actually held is credited - no phantom proceeds.
    assert after["account"]["cash"] == pytest.approx(
        before["account"]["cash"] + price * 2, abs=0.05
    )
