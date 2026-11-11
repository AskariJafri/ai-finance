"""Same question, same day, same answer — and a plan that outlives its screen."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from app.core.config import settings as app_settings
from app.schemas.invest import ActionPlanIn
from app.services.portfolio_actions_service import PortfolioActionsService
from tests.test_portfolio_actions import (
    ScriptedLLM,
    SignalProvider,
    _cash,
    _hold,
    _user,
    _verdict,
)

PREFIX = "/api/v1"


class FickleLLM(ScriptedLLM):
    """Gives a different answer every time it is asked — the complaint."""

    def __init__(self):
        super().__init__({})
        self.calls = 0

    async def complete(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        held = "ALREADY OWNED" in (kwargs.get("system") or "")
        choices = ("ADD", "HOLD", "TRIM") if held else ("BUY", "SKIP")
        return _verdict(choices[self.calls % len(choices)], 0.5 + 0.1 * (self.calls % 4))


def _shape(plan):
    return (
        [(a.symbol, a.side, a.action, a.amount) for a in plan.actions],
        sorted((h.symbol, h.reason) for h in plan.holds),
    )


@pytest.mark.asyncio
async def test_asking_twice_in_a_day_gets_the_same_plan(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "AAPL", 10, 5_000)
    await _hold(seeded_session, user_id, "XEQT.TO", 10, 5_000)
    await _cash(seeded_session, user_id, 2_000)

    llm = FickleLLM()
    provider = SignalProvider(declining={"AAPL"})
    body = ActionPlanIn(max_new_positions=2)

    first = await PortfolioActionsService(seeded_session, provider, llm).plan(user_id, body)
    asked = llm.calls
    second = await PortfolioActionsService(seeded_session, provider, llm).plan(user_id, body)

    assert asked > 0
    assert llm.calls == asked, "today's verdicts are reused, not asked again"
    assert _shape(first) == _shape(second)
    assert first.period == second.period


@pytest.mark.asyncio
async def test_plan_lines_carry_the_signal_they_were_anchored_to(seeded_session):
    user_id = await _user(seeded_session)
    await _hold(seeded_session, user_id, "AAPL", 10, 5_000)
    await _cash(seeded_session, user_id, 1_000)

    plan = await PortfolioActionsService(
        seeded_session, SignalProvider(), ScriptedLLM({}, default=("HOLD", 0.6))
    ).plan(user_id, ActionPlanIn(max_new_positions=0))
    lines = [*plan.actions, *plan.holds]
    aapl = next(x for x in lines if x.symbol == "AAPL")
    assert aapl.signal_score is not None
    assert "trend score" in (aapl.signal or "")


# --- the background run -------------------------------------------------------
@pytest.fixture
def _mock_market(monkeypatch):
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)


async def _register(client: AsyncClient) -> dict[str, str]:
    resp = await client.post(
        f"{PREFIX}/auth/register", json={"email": "runs@test.com", "password": "supersecret123"}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _wait_done(client: AsyncClient, headers: dict) -> dict:
    for _ in range(200):
        latest = (await client.get(f"{PREFIX}/invest/actions/runs/latest", headers=headers)).json()
        if latest and latest["status"] != "running":
            return latest
        await asyncio.sleep(0.05)
    raise AssertionError("run never finished")


@pytest.mark.asyncio
@pytest.mark.usefixtures("_mock_market")
async def test_a_plan_runs_in_the_background_and_is_reused(client: AsyncClient):
    headers = await _register(client)
    assert (
        await client.get(f"{PREFIX}/invest/actions/runs/latest", headers=headers)
    ).json() is None

    await client.put(
        f"{PREFIX}/invest/holdings",
        headers=headers,
        json={"symbol": "VFV.TO", "qty": 10, "avg_cost": 60.0},
    )
    body = {"added_cash": 0, "max_new_positions": 0, "use_ai": False, "sizing": "dollars"}

    started = await client.post(f"{PREFIX}/invest/actions/runs", headers=headers, json=body)
    assert started.status_code == 200, started.text
    run = started.json()
    assert run["status"] in {"running", "done"}

    done = await _wait_done(client, headers)
    assert done["id"] == run["id"]
    assert done["status"] == "done", done.get("error")
    assert done["result"]["holds"] or done["result"]["actions"]

    # Same inputs, same holdings, same period: the same plan, not a new one.
    again = (await client.post(f"{PREFIX}/invest/actions/runs", headers=headers, json=body)).json()
    assert again["id"] == run["id"] and again["reused"] is True

    # ...unless asked to rebuild.
    forced = (
        await client.post(
            f"{PREFIX}/invest/actions/runs", headers=headers, json={**body, "force": True}
        )
    ).json()
    assert forced["id"] != run["id"]
    await _wait_done(client, headers)

    one = await client.get(f"{PREFIX}/invest/actions/runs/{run['id']}", headers=headers)
    assert one.status_code == 200 and one.json()["status"] == "done"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_mock_market")
async def test_a_failed_plan_reports_why(client: AsyncClient):
    headers = await _register(client)
    body = {"max_new_positions": 0, "use_ai": False}
    await client.post(f"{PREFIX}/invest/actions/runs", headers=headers, json=body)
    done = await _wait_done(client, headers)
    assert done["status"] == "failed"
    assert "holdings" in done["error"].lower()
