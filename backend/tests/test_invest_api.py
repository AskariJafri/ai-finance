"""E2E invest flow: manual holdings + mock market data (no network, no broker).

Covers the Canadian setup: TSX tickers priced in CAD, US tickers converted via
the cached USDCAD rate, and the rebalance CHECKLIST (propose -> mark executed
at your own broker -> holdings update).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings as app_settings

PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def _mock_market_data(monkeypatch):
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)


async def _register(client: AsyncClient, email: str = "inv@test.com") -> dict[str, str]:
    resp = await client.post(
        f"{PREFIX}/auth/register", json={"email": email, "password": "supersecret123"}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_invest_full_flow(client: AsyncClient):
    headers = await _register(client)

    # 1. No holdings yet -> graceful empty state, not an error.
    pf = await client.get(f"{PREFIX}/invest/portfolio", headers=headers)
    assert pf.status_code == 200
    assert pf.json()["connected"] is False

    # 2. Symbol lookup validates the ticker and reports its currency.
    lookup = await client.get(f"{PREFIX}/invest/lookup?symbol=VFV.TO", headers=headers)
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["currency"] == "CAD"

    bad = await client.get(f"{PREFIX}/invest/lookup?symbol=NOPE", headers=headers)
    assert bad.status_code == 422  # unknown ticker -> friendly error

    # 3. Add holdings: a Canadian ETF (CAD) and a US stock (USD).
    for symbol, qty, cost in [("VFV.TO", 100, 60.0), ("AAPL", 20, 150.0)]:
        resp = await client.put(
            f"{PREFIX}/invest/holdings",
            headers=headers,
            json={"symbol": symbol, "qty": qty, "avg_cost": cost},
        )
        assert resp.status_code == 200, resp.text
    await client.put(f"{PREFIX}/invest/cash", headers=headers, json={"cash": 2000})

    holdings = (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json()
    assert {h["symbol"] for h in holdings} == {"VFV.TO", "AAPL"}
    assert next(h for h in holdings if h["symbol"] == "VFV.TO")["currency"] == "CAD"
    assert next(h for h in holdings if h["symbol"] == "AAPL")["currency"] == "USD"

    # 4. Sync is idempotent (adding a holding already cached its bars), and the
    #    portfolio prices everything in CAD.
    sync = await client.post(f"{PREFIX}/invest/sync", headers=headers)
    assert sync.status_code == 200, sync.text
    assert sync.json()["failed_symbols"] == []
    assert sync.json()["data_start"] is not None

    pf = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    assert pf["connected"] is True
    assert pf["currency"] == "CAD"
    assert pf["usd_cad_rate"] > 1  # USD holdings converted
    assert pf["account"]["cash"] == 2000.0
    assert pf["account"]["equity"] == pytest.approx(pf["account"]["invested"] + 2000.0, abs=0.02)
    weights = sum(p["weight"] for p in pf["positions"])
    assert weights == pytest.approx(1.0, abs=0.01)

    # 5. Risk metrics compute off the cached bars.
    risk = (await client.get(f"{PREFIX}/invest/risk?window=1y", headers=headers)).json()
    assert risk["reason"] is None
    assert risk["portfolio"]["ann_vol"] is not None
    assert risk["portfolio"]["var_95_amount"] > 0
    assert risk["concentration"]["hhi"] >= 0.5  # two positions
    assert len(risk["correlation"]["symbols"]) == 2

    # 6. Backtest / projection / scenarios.
    backtest = await client.post(
        f"{PREFIX}/invest/backtest",
        headers=headers,
        json={"strategy": "buy_hold", "symbols": ["VFV.TO"], "start": "2020-01-01"},
    )
    assert backtest.status_code == 200, backtest.text
    assert backtest.json()["metrics"]["cagr"] is not None

    projection = await client.post(
        f"{PREFIX}/invest/projection",
        headers=headers,
        json={"horizon_years": 1, "method": "gbm", "n_paths": 200, "seed": 42},
    )
    assert projection.status_code == 200, projection.text
    bands = projection.json()["bands"]
    assert bands["p5"][-1] <= bands["p50"][-1] <= bands["p95"][-1]

    scenarios = (await client.get(f"{PREFIX}/invest/scenarios", headers=headers)).json()
    gfc = next(s for s in scenarios if s["key"] == "gfc_2008")
    assert gfc["portfolio_impact"]["amount"] < 0
    assert gfc["index_recovery"]["months"] == 49

    # 7. Targets -> propose -> mark one executed (holdings update) -> reject one.
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "VFV.TO", "weight": 0.7}, {"symbol": "AAPL", "weight": 0.3}],
    )
    proposal = await client.post(
        f"{PREFIX}/invest/rebalance/propose",
        headers=headers,
        json={"drift_threshold": 0.01},
    )
    assert proposal.status_code == 200, proposal.text
    orders = proposal.json()["orders"]
    assert len(orders) >= 1
    for o in orders:
        assert o["status"] == "proposed"
        assert o["reason"] and o["qty"] > 0

    sell = next((o for o in orders if o["side"] == "sell"), None)
    if sell:
        before = next(
            h["qty"]
            for h in (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json()
            if h["symbol"] == sell["symbol"]
        )
        done = await client.post(
            f"{PREFIX}/invest/orders/proposed/{sell['id']}/executed",
            headers=headers,
            json={},
        )
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "filled"
        after = next(
            (
                h["qty"]
                for h in (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json()
                if h["symbol"] == sell["symbol"]
            ),
            0.0,
        )
        assert after == pytest.approx(before - sell["qty"], abs=1e-6)

        # Marking it twice is rejected.
        again = await client.post(
            f"{PREFIX}/invest/orders/proposed/{sell['id']}/executed",
            headers=headers,
            json={},
        )
        assert again.status_code == 422

    buy = next((o for o in orders if o["side"] == "buy"), None)
    if buy:
        rejected = await client.post(
            f"{PREFIX}/invest/orders/proposed/{buy['id']}/reject", headers=headers
        )
        assert rejected.json()["status"] == "rejected"

    # 8. Explainers.
    explainers = (await client.get(f"{PREFIX}/invest/explainers", headers=headers)).json()
    for key in ("var_95", "sharpe", "monte_carlo", "rebalancing"):
        assert explainers[key]["title"] and explainers[key]["body"]

    # 9. Deleting the last holdings returns the empty state.
    for h in (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json():
        assert (
            await client.delete(f"{PREFIX}/invest/holdings/{h['id']}", headers=headers)
        ).status_code == 204
    pf = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    assert pf["connected"] is False


@pytest.mark.asyncio
async def test_buy_order_creates_holding_with_cost_basis(client: AsyncClient):
    headers = await _register(client, "buy@test.com")
    # Hold only cash + one position, target a symbol we don't own yet.
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "VFV.TO", "qty": 100, "avg_cost": 60.0},
    )
    await client.put(f"{PREFIX}/invest/cash", headers=headers, json={"cash": 5000})
    await client.post(f"{PREFIX}/invest/sync", headers=headers)
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "VFV.TO", "weight": 0.6}, {"symbol": "ZAG.TO", "weight": 0.4}],
    )
    proposal = await client.post(
        f"{PREFIX}/invest/rebalance/propose", headers=headers, json={"drift_threshold": 0.02}
    )
    assert proposal.status_code == 200, proposal.text
    buy = next(o for o in proposal.json()["orders"] if o["symbol"] == "ZAG.TO")
    assert buy["side"] == "buy"

    done = await client.post(
        f"{PREFIX}/invest/orders/proposed/{buy['id']}/executed",
        headers=headers,
        json={"fill_price": 15.5},
    )
    assert done.status_code == 200, done.text

    holdings = (await client.get(f"{PREFIX}/invest/holdings", headers=headers)).json()
    zag = next(h for h in holdings if h["symbol"] == "ZAG.TO")
    assert zag["qty"] == pytest.approx(buy["qty"], abs=1e-6)
    assert zag["avg_cost"] == pytest.approx(15.5, abs=0.01)  # the fill price we gave


@pytest.mark.asyncio
async def test_targets_validation(client: AsyncClient):
    headers = await _register(client, "tv@test.com")
    bad = await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "VFV.TO", "weight": 0.5}, {"symbol": "AAPL", "weight": 0.4}],
    )
    assert bad.status_code == 422  # must sum to 100%


@pytest.mark.asyncio
async def test_a_fractional_position_is_never_silently_dropped(client: AsyncClient):
    """A sub-one-share holding must appear in the plan, not vanish from it.

    Fractional shares are normal at Wealthsimple, and whole-share sizing rounds
    them to zero. Skipping on that quietly means a position you own is missing
    from every rebalance with no explanation — which reads as "nothing to do
    here" rather than "this tool can't express it".
    """
    headers = await _register(client, "frac@test.com")

    # 0.68 shares — exactly the shape a fractional-share broker produces.
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "AAPL", "qty": 0.68, "avg_cost": 200.0},
    )
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "VFV.TO", "qty": 10, "avg_cost": 100.0},
    )
    # An explicit 0% target on AAPL: "I hold this and want none of it", so the
    # whole position should be sold. Omitting it instead would mean AAPL is
    # simply unmanaged, which this planner now leaves alone.
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "VFV.TO", "weight": 1.0}, {"symbol": "AAPL", "weight": 0.0}],
    )

    dollars = await client.post(
        f"{PREFIX}/invest/rebalance/propose",
        headers=headers,
        json={"drift_threshold": 0.01, "sizing": "dollars"},
    )
    assert dollars.status_code == 200, dollars.text
    assert any(o["symbol"] == "AAPL" for o in dollars.json()["orders"])

    shares = await client.post(
        f"{PREFIX}/invest/rebalance/propose",
        headers=headers,
        json={"drift_threshold": 0.01, "sizing": "shares"},
    )
    assert shares.status_code == 200, shares.text
    body = shares.json()
    sell = next((o for o in body["orders"] if o["symbol"] == "AAPL"), None)
    # Selling a fractional position out entirely is a real order — a broker that
    # sold you 0.68 of a share will buy it back.
    assert sell is not None, "the fractional holding disappeared from the plan"
    assert sell["side"] == "sell"
    assert sell["qty"] == pytest.approx(0.68)
    assert sell["est_value"] > 0


@pytest.mark.asyncio
async def test_a_partial_sale_that_cannot_be_whole_shares_is_explained(client: AsyncClient):
    """When whole shares genuinely can't express a trade, say which and why."""
    headers = await _register(client, "frac2@test.com")

    # Trimming part of a fractional position is not expressible in whole shares.
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "AAPL", "qty": 0.9, "avg_cost": 200.0},
    )
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "VFV.TO", "qty": 10, "avg_cost": 100.0},
    )
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "AAPL", "weight": 0.05}, {"symbol": "VFV.TO", "weight": 0.95}],
    )

    resp = await client.post(
        f"{PREFIX}/invest/rebalance/propose",
        headers=headers,
        json={"drift_threshold": 0.01, "sizing": "shares"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if not any(o["symbol"] == "AAPL" for o in body["orders"]):
        assert "AAPL" in body["note"]
        assert "dollar amounts" in body["note"].lower()


@pytest.mark.asyncio
async def test_a_zero_target_is_a_valid_answer(client: AsyncClient):
    """ "Keep none of this" has to be expressible.

    Rejecting a 0% weight forces the holding to be left out of the target set
    instead — and a holding with no target row silently disappears from the
    editor, which is how a position ends up marked for full liquidation without
    anyone choosing that.
    """
    headers = await _register(client, "zerotarget@test.com")
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "AAPL", "qty": 5, "avg_cost": 200.0},
    )

    resp = await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "AAPL", "weight": 0.0}, {"symbol": "VFV.TO", "weight": 1.0}],
    )
    assert resp.status_code == 200, resp.text
    saved = {t["symbol"]: t["weight"] for t in resp.json()["targets"]}
    assert saved["AAPL"] == 0.0


@pytest.mark.asyncio
async def test_a_trade_conserves_the_account_total(client: AsyncClient):
    """A trade is an exchange, not a deletion.

    Selling converts shares into cash and buying does the reverse. Moving only
    the shares makes every executed sell destroy its own proceeds, so the
    account total silently drops by the size of each trade — which is exactly
    what a user notices when the app disagrees with their broker.
    """
    headers = await _register(client, "conserve@test.com")
    for symbol in ("VFV.TO", "XEQT.TO"):
        await client.put(
            f"{PREFIX}/invest/holdings",
            headers=headers,
            json={"symbol": symbol, "qty": 10, "avg_cost": 100.0},
        )
    await client.put(f"{PREFIX}/invest/cash", headers=headers, json={"cash": 500})
    # An explicit 0% target on XEQT, so the rebalancer proposes selling all of
    # it — an omitted symbol is unmanaged and would be left alone instead.
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "VFV.TO", "weight": 1.0}, {"symbol": "XEQT.TO", "weight": 0.0}],
    )

    before = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    equity_before = before["account"]["equity"]
    price = next(p["price"] for p in before["positions"] if p["symbol"] == "XEQT.TO")

    proposal = await client.post(
        f"{PREFIX}/invest/rebalance/propose",
        headers=headers,
        json={"drift_threshold": 0.01, "sizing": "shares"},
    )
    # Fill at the price the portfolio is valued at, so only the accounting can
    # move the total.
    sells = [o for o in proposal.json()["orders"] if o["side"] == "sell"]
    assert sells, "expected a sell for the untargeted holding"
    order = sells[0]

    resp = await client.post(
        f"{PREFIX}/invest/orders/proposed/{order['id']}/executed",
        headers=headers,
        json={"fill_price": price},
    )
    assert resp.status_code == 200, resp.text

    after = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    assert after["account"]["equity"] == pytest.approx(equity_before, abs=0.05)
    assert after["account"]["cash"] > before["account"]["cash"]


@pytest.mark.asyncio
async def test_a_bought_position_is_paid_for_out_of_cash(client: AsyncClient):
    headers = await _register(client, "paidfor@test.com")
    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "VFV.TO", "qty": 10, "avg_cost": 100.0},
    )
    await client.put(f"{PREFIX}/invest/cash", headers=headers, json={"cash": 5000})

    before = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    price = next(p["price"] for p in before["positions"] if p["symbol"] == "VFV.TO")

    plan = await client.post(
        f"{PREFIX}/invest/deploy/accept",
        headers=headers,
        json={
            "orders": [{"symbol": "VFV.TO", "qty": 2, "price": price, "reason": "test"}],
            "sizing": "shares",
        },
    )
    assert plan.status_code == 200, plan.text
    order = plan.json()["orders"][0]

    resp = await client.post(
        f"{PREFIX}/invest/orders/proposed/{order['id']}/executed",
        headers=headers,
        json={"fill_price": price},
    )
    assert resp.status_code == 200, resp.text

    after = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    assert after["account"]["cash"] < before["account"]["cash"]
    assert after["account"]["equity"] == pytest.approx(before["account"]["equity"], abs=0.05)


@pytest.mark.asyncio
async def test_a_holding_with_no_target_is_left_alone_not_liquidated(client: AsyncClient):
    """The two planners must not contradict each other.

    Reported from a live account: the AI actions planner recommended buying
    AVGO, the user bought it, and the very next rebalance said sell AVGO and
    buy NVDA. Neither planner was malfunctioning — the actions planner shops
    names you do not own, ``accept`` never writes a target row, and rebalance
    read that missing row as a 0% target. So every name the committee ever
    recommended came back marked for liquidation the moment it was bought.

    A missing target means unmanaged. Only an explicit 0% means sell out.
    """
    headers = await _register(client, "avgo@test.com")
    for symbol, qty in (("VFV.TO", 10), ("XEQT.TO", 10), ("AAPL", 2)):
        await client.put(
            f"{PREFIX}/invest/holdings",
            headers=headers,
            json={"symbol": symbol, "qty": qty, "avg_cost": 100.0},
        )
    # AAPL is the newly bought name: held, but absent from the target mix.
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "VFV.TO", "weight": 0.5}, {"symbol": "XEQT.TO", "weight": 0.5}],
    )

    body = (
        await client.post(
            f"{PREFIX}/invest/rebalance/propose",
            headers=headers,
            json={"drift_threshold": 0.01, "sizing": "dollars"},
        )
    ).json()

    assert not [o for o in body["orders"] if o["symbol"] == "AAPL"], (
        "a holding with no target row must never be proposed for sale"
    )
    # Silence would read as "on target" — the plan has to say why it is absent.
    assert "AAPL" in body["note"]
    assert "no target weight" in body["note"]

    # And the targeted names are measured against the sleeve they govern, not
    # against an account that still counts the position being left alone — with
    # AAPL in the denominator both would read underweight by its size.
    #
    # The sleeve is self-funding: with no cash in the account, what it sells is
    # exactly what it buys. A single dollar of difference would mean AAPL's
    # value had leaked into the target math after all.
    assert body["total_buy"] == pytest.approx(body["total_sell"], abs=0.02)
    assert body["total_buy"] > 0


@pytest.mark.asyncio
async def test_an_explicit_zero_target_still_sells_out(client: AsyncClient):
    """The escape hatch the fix above depends on."""
    headers = await _register(client, "zerotarget@test.com")
    for symbol, qty in (("VFV.TO", 10), ("AAPL", 2)):
        await client.put(
            f"{PREFIX}/invest/holdings",
            headers=headers,
            json={"symbol": symbol, "qty": qty, "avg_cost": 100.0},
        )
    await client.put(
        f"{PREFIX}/invest/targets",
        headers=headers,
        json=[{"symbol": "VFV.TO", "weight": 1.0}, {"symbol": "AAPL", "weight": 0.0}],
    )

    body = (
        await client.post(
            f"{PREFIX}/invest/rebalance/propose",
            headers=headers,
            json={"drift_threshold": 0.01, "sizing": "dollars"},
        )
    ).json()
    sells = [o for o in body["orders"] if o["symbol"] == "AAPL" and o["side"] == "sell"]
    assert sells, "an explicit 0% target is a decision and must still sell out"
    assert "no target weight" not in body["note"]
