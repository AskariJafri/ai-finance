"""Test fixtures: in-memory SQLite DB + ASGI client with overridden session.

Tests never touch Postgres or the network. The Plaid gateway auto-resolves to
``MockPlaidClient`` because no credentials are configured, and Ollama is simply
unavailable (categorizer degrades gracefully) unless a test injects a fake LLM.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.repositories.rules import CategoryRuleRepository

TEST_URL = "sqlite+aiosqlite:///:memory:"


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
