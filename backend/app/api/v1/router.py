from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    advice,
    auth,
    brokerage,
    categories,
    chat,
    dashboard,
    invest,
    plaid,
    reports,
    settings,
    trader,
    transactions,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(plaid.router)
api_router.include_router(accounts.router)
api_router.include_router(transactions.router)
# New canonical home for category CRUD. A router of its own (rather than
# nesting under /transactions) sidesteps that router's path-ordering hazard;
# GET /transactions/categories stays as a thin alias for one release.
api_router.include_router(categories.router)
api_router.include_router(dashboard.router)
api_router.include_router(reports.router)
api_router.include_router(advice.router)
api_router.include_router(chat.router)
api_router.include_router(invest.router)
api_router.include_router(brokerage.router)
api_router.include_router(trader.router)
api_router.include_router(settings.router)
