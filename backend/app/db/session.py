"""Async database engine and session factory + FastAPI dependency.

A single engine/sessionmaker is created per process. ``get_session`` yields a
short-lived ``AsyncSession`` scoped to a request and guarantees rollback on
error and close on completion.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Managed Postgres requires TLS, and the connection string cannot say so:
# ``sslmode`` is a libpq parameter that asyncpg rejects, so the settings layer
# strips it from the URL and we re-express the intent here.
_connect_args: dict[str, object] = (
    {"ssl": "require"} if settings.database_ssl_required else {}
)

# Behind PgBouncer a connection is not ours between statements, so a prepared
# statement can be created on one backend and executed on another. Both caches
# have to go: asyncpg's own, and the one SQLAlchemy's dialect keeps above it.
if settings.database_pgbouncer:
    _connect_args |= {"statement_cache_size": 0, "prepared_statement_cache_size": 0}

# Pooling is a property of the host, not of the code. A container holds
# connections open between requests and wants them verified and recycled; a
# serverless instance should hold none at all, and lets the external pooler do
# the pooling.
_pool_kwargs: dict[str, object] = (
    {"poolclass": NullPool}
    if settings.database_null_pool
    else {
        # Free-tier Postgres suspends its compute when idle and drops the
        # pooled connections with it, so verify before use and recycle well
        # before the provider does.
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args=_connect_args,
    **_pool_kwargs,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
