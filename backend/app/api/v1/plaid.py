from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, PlaidServiceDep
from app.core.logging import get_logger
from app.schemas.plaid import (
    ExchangeRequest,
    ExchangeResponse,
    LinkTokenResponse,
    SyncResponse,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/plaid", tags=["plaid"])


@router.post("/link/token", response_model=LinkTokenResponse)
async def create_link_token(user: CurrentUser, plaid: PlaidServiceDep) -> LinkTokenResponse:
    return await plaid.create_link_token(user.id)


@router.post("/link/exchange", response_model=ExchangeResponse)
async def exchange_public_token(
    body: ExchangeRequest, user: CurrentUser, plaid: PlaidServiceDep
) -> ExchangeResponse:
    return await plaid.exchange_public_token(
        user_id=user.id,
        public_token=body.public_token,
        institution_id=body.institution_id,
        institution_name=body.institution_name,
    )


@router.post("/sync", response_model=SyncResponse)
async def sync(user: CurrentUser, plaid: PlaidServiceDep) -> SyncResponse:
    return await plaid.sync_all(user.id)


@router.post("/webhook", status_code=202)
async def webhook(request: Request) -> dict[str, str]:
    """Plaid webhook receiver.

    Phase 1 acknowledges and logs. Verifying the JWT signature and triggering an
    async re-sync of the affected item lands in the notifications/jobs phase.
    """
    payload = await request.json()
    logger.info(
        "Plaid webhook received",
        extra={"extra": {"webhook_type": payload.get("webhook_type")}},
    )
    return {"status": "accepted"}
