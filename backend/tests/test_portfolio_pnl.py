"""Unrealized and day-over-day P/L on the portfolio endpoint.

The roll-up on ``account`` has to agree with the position rows it summarises,
and a day change must be absent — not zero — when there is no prior session
cached to compare against.
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


@pytest.mark.asyncio
async def test_portfolio_reports_unrealized_and_day_pnl(client: AsyncClient):
    headers = await _register(client, "pnl@test.com")

    for symbol, qty, cost in [("VFV.TO", 100, 60.0), ("AAPL", 20, 150.0)]:
        resp = await client.put(
            f"{PREFIX}/invest/holdings",
            headers=headers,
            json={"symbol": symbol, "qty": qty, "avg_cost": cost},
        )
        assert resp.status_code == 200, resp.text
    await client.put(f"{PREFIX}/invest/cash", headers=headers, json={"cash": 2000})

    pf = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    account, positions = pf["account"], pf["positions"]
    assert pf["prices_as_of"] is not None

    # Every row carries the numbers the UI renders.
    for p in positions:
        assert p["cost_basis"] > 0
        assert p["unrealized_pl"] == pytest.approx(p["market_value"] - p["cost_basis"], abs=0.02)
        assert p["unrealized_pl_pct"] == pytest.approx(
            p["market_value"] / p["cost_basis"] - 1.0, abs=1e-3
        )
        assert p["price_date"] <= pf["as_of"]
        # Mock bars span years, so both symbols have a previous session.
        assert p["prev_close"] is not None
        assert p["day_change"] == pytest.approx(p["price"] - p["prev_close"], abs=1e-3)
        assert p["day_change_pct"] == pytest.approx(p["day_change"] / p["prev_close"], abs=1e-3)
        # day_pl is CAD; day_change is in the listing currency.
        to_cad = pf["usd_cad_rate"] if p["currency"] == "USD" else 1.0
        assert p["day_pl"] == pytest.approx(p["day_change"] * p["qty"] * to_cad, abs=0.05)

    # The roll-up agrees with the rows, and cash is excluded from cost basis.
    assert account["cost_basis"] == pytest.approx(sum(p["cost_basis"] for p in positions), abs=0.02)
    assert account["unrealized_pl"] == pytest.approx(
        account["invested"] - account["cost_basis"], abs=0.02
    )
    assert account["unrealized_pl_pct"] == pytest.approx(
        account["unrealized_pl"] / account["cost_basis"], abs=1e-3
    )
    assert account["day_pl"] == pytest.approx(sum(p["day_pl"] for p in positions), abs=0.05)
    # Day % is measured against yesterday's invested value, not today's equity.
    assert account["day_pl_pct"] == pytest.approx(
        account["day_pl"] / (account["invested"] - account["day_pl"]), abs=1e-3
    )


@pytest.mark.asyncio
async def test_day_change_is_null_without_a_prior_session(client: AsyncClient, monkeypatch):
    """One cached bar means no yesterday — that reads as unknown, not flat."""
    headers = await _register(client, "oneday@test.com")
    resp = await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "VFV.TO", "qty": 10, "avg_cost": 60.0},
    )
    assert resp.status_code == 200, resp.text

    from app.repositories.price_bars import PriceBarRepository

    real = PriceBarRepository.recent_closes

    async def only_latest(self, symbol: str, limit: int = 2):
        return (await real(self, symbol, limit))[-1:]

    monkeypatch.setattr(PriceBarRepository, "recent_closes", only_latest)

    pf = (await client.get(f"{PREFIX}/invest/portfolio", headers=headers)).json()
    position = pf["positions"][0]
    assert position["prev_close"] is None
    assert position["day_change"] is None
    assert position["day_change_pct"] is None
    assert position["day_pl"] is None
    assert pf["account"]["day_pl"] is None
    assert pf["account"]["day_pl_pct"] is None
    # Unrealized P/L still works — it needs only today's price and the cost.
    assert position["unrealized_pl"] != 0
    assert pf["account"]["unrealized_pl"] == pytest.approx(position["unrealized_pl"], abs=0.02)
