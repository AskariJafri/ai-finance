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
