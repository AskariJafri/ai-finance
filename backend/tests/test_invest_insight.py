"""Invest AI health check: caching semantics + fallback-never-cached."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings as app_settings
from app.integrations.marketdata.mock_client import MockMarketData
from app.repositories.users import UserRepository
from app.schemas.invest import HoldingIn
from app.services.invest_insight_service import InvestInsightService
from app.services.invest_service import InvestService
from tests.test_advice import FailingLLM, FakeLLM


async def _user_with_holdings(session) -> tuple[uuid.UUID, InvestService]:
    user = await UserRepository(session).create(
        email=f"i{uuid.uuid4().hex[:6]}@test.com", password_hash="x", full_name=None
    )
    await session.flush()
    invest = InvestService(session, MockMarketData())
    await invest.upsert_holding(user.id, HoldingIn(symbol="VFV.TO", qty=100, avg_cost=60))
    await invest.upsert_holding(user.id, HoldingIn(symbol="AAPL", qty=20, avg_cost=150))
    await invest.sync(user.id)
    return user.id, invest


@pytest.mark.asyncio
async def test_health_insight_caches_and_falls_back(seeded_session, monkeypatch):
    monkeypatch.setattr(app_settings, "market_data_use_mock", True)
    user_id, invest = await _user_with_holdings(seeded_session)

    fake = FakeLLM(response="Your portfolio is balanced.")
    svc = InvestInsightService(seeded_session, fake, invest)

    assert (await svc.get(user_id)).source == "none"

    out = await svc.generate(user_id)
    assert out.source == "ai"
    assert fake.calls == 1

    # Cached: no second LLM call.
    out = await svc.generate(user_id)
    assert fake.calls == 1
    assert out.source == "ai"

    # LLM failure degrades to deterministic tips and never overwrites the cache.
    svc_fail = InvestInsightService(seeded_session, FailingLLM(), invest)
    out = await svc_fail.generate(user_id, force=True)
    assert out.source == "fallback"
    assert out.content
    assert (await svc_fail.get(user_id)).source == "ai"
