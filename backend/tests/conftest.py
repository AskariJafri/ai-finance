"""Test fixtures: in-memory SQLite DB + ASGI client with overridden session.

Tests never touch Postgres or the network. The Plaid gateway auto-resolves to
``MockPlaidClient`` because no credentials are configured, and the LLM is simply
unconfigured (categorizer degrades gracefully) unless a test injects a fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.ai.factory import get_llm_provider
from app.ai.llm.openai_compat import reset_quota_breaker
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.repositories.rules import CategoryRuleRepository

TEST_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _llm_configured(monkeypatch):
    """Make ``settings.ai_available`` track ``ai_enabled`` alone, and keep the
    suite hermetic.

    The AI paths are gated on a key being present, so without this every test
    that injects a fake LLM would silently take the fallback branch instead.
    Nothing reaches the network — the key is never used by a fake.

    ``llm_fallbacks`` is emptied and the provider cache cleared because both
    outlive a test: a developer with a real fallback key in their ``.env``
    would otherwise have the suite build a pool pointed at a live provider, and
    ``get_llm_provider`` is ``lru_cache``d, so the first test to touch it would
    fix that pool in place for every test after it. The quota breaker is module
    state for the same reason.
    """
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_fallbacks", [])
    get_llm_provider.cache_clear()
    reset_quota_breaker()
    yield
    get_llm_provider.cache_clear()
    reset_quota_breaker()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        TEST_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def sessionmaker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(sessionmaker) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session


@pytest_asyncio.fixture
async def seeded_session(db_session: AsyncSession) -> AsyncSession:
    await CategoryRuleRepository(db_session).ensure_seed()
    await db_session.commit()
    return db_session


@pytest_asyncio.fixture
async def client(sessionmaker) -> AsyncIterator[AsyncClient]:
    # Seed reference data once for the API surface.
    async with sessionmaker() as session:
        await CategoryRuleRepository(session).ensure_seed()
        await session.commit()

    app = create_app()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
