"""FastAPI application factory.

Wires middleware, the versioned API router, a uniform error handler for
``ServiceError`` subclasses, and a startup hook that idempotently seeds the
default category taxonomy + rules.
"""

from __future__ import annotations

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(logging.DEBUG if settings.debug else logging.INFO)
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    try:
        await seed_reference_data()
    except Exception as exc:  # pragma: no cover - startup resilience
        logger.warning("Seeding skipped/failed (DB not ready?): %s", exc)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
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
