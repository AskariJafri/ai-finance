"""FastAPI application factory.

Wires middleware, the versioned API router, a uniform error handler for
``ServiceError`` subclasses, and a startup hook that idempotently seeds the
default category taxonomy + rules.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.repositories.rules import CategoryRuleRepository
from app.services.errors import ServiceError

logger = get_logger(__name__)


async def seed_reference_data() -> None:
    """Insert system categories + seed rules if absent."""
    async with SessionLocal() as session:
        await CategoryRuleRepository(session).ensure_seed()
        await session.commit()
    logger.info("Reference data seeded")


async def seed_with_retry(attempts: int = 5, delay: float = 2.0) -> None:
    """Seed, tolerating a database that is still waking up.

    Free-tier Postgres suspends its compute when idle, so the first connection
    after a deploy can time out. Retrying matters more than it looks: a single
    swallowed failure boots the app with no categories seeded and only a warning
    in the log, and categorization then misbehaves until the next restart
    happens to catch the database awake.
    """
    for attempt in range(1, attempts + 1):
        try:
            await seed_reference_data()
            return
        except Exception as exc:  # noqa: BLE001 - startup must not hard-fail
            if attempt == attempts:
                logger.error("Seeding failed after %d attempts: %s", attempts, exc)
                return
            logger.warning("Seeding attempt %d failed (%s); retrying", attempt, exc)
            await asyncio.sleep(delay * attempt)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(logging.DEBUG if settings.debug else logging.INFO)
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    # Seeding belongs to a boot, and on a serverless host "boot" happens per
    # cold start — each one paying a write, and up to twenty seconds of retry
    # sleep, for work the previous instance already did. There the deploy's
    # build step seeds once and this is switched off.
    if settings.seed_on_startup:
        await seed_with_retry()
    else:
        logger.info("Startup seeding disabled (SEED_ON_STARTUP=false)")
    yield


def create_app() -> FastAPI:
    # A deployed instance is on a public URL, and the schema names every
    # endpoint including the brokerage ones. Auth still guards the data; this
    # just declines to publish the map.
    public = settings.environment != "production"

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if public else None,
        openapi_url="/openapi.json" if public else None,
    )

    # Wildcard and credentials must never ship together. Starlette does not
    # send a literal "*" when credentials are allowed — it echoes whatever
    # Origin asked, so `allow_origins=["*"]` answers a request from any site
    # with `Allow-Origin: <that site>` and `Allow-Credentials: true`. Today the
    # only auth is a Bearer header that a cross-origin page cannot read or
    # attach, so nothing is exploitable through it; the day anyone adds a
    # session cookie, it silently becomes the whole account.
    #
    # Credentials are not used, so the honest configuration is to say so, and
    # to keep the wildcard usable for local work without it meaning anything
    # more than it should.
    allow_origins = settings.cors_origin_list
    wildcard = "*" in allow_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=not wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    from app.api.v1.router import api_router

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
