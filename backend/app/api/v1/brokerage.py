"""Brokerage linking and live order execution.

Route layout mirrors the Plaid link flow: ``/link`` hands back a URL the client
opens, ``/link/complete`` is called once the user returns.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import BrokerageServiceDep, CurrentUser
from app.schemas.brokerage import (
    BrokerageConnectionOut,
    BrokerageLinkIn,
    BrokerageLinkOut,
    BrokerageSyncOut,
)
from app.schemas.invest import ProposedOrderOut

router = APIRouter(prefix="/brokerage", tags=["brokerage"])


@router.post("/link", response_model=BrokerageLinkOut)
async def start_link(
    user: CurrentUser,
    service: BrokerageServiceDep,
    body: BrokerageLinkIn | None = None,
) -> BrokerageLinkOut:
    """Begin linking. Returns the SnapTrade portal URL for the client to open."""
    return await service.start_link(user.id, (body or BrokerageLinkIn()).redirect_uri)


@router.post("/link/complete", response_model=list[BrokerageConnectionOut])
async def complete_link(
    user: CurrentUser, service: BrokerageServiceDep
) -> list[BrokerageConnectionOut]:
    """Called after the portal returns — stores the authorized accounts."""
    return await service.complete_link(user.id)


@router.get("/connections", response_model=list[BrokerageConnectionOut])
async def list_connections(
    user: CurrentUser, service: BrokerageServiceDep
) -> list[BrokerageConnectionOut]:
    return await service.list_connections(user.id)


@router.delete("/connections", status_code=status.HTTP_204_NO_CONTENT)
async def unlink(user: CurrentUser, service: BrokerageServiceDep) -> None:
    """Unlink every brokerage account. Manually-entered holdings are kept."""
    await service.unlink(user.id)


@router.post("/sync", response_model=BrokerageSyncOut)
async def sync_positions(user: CurrentUser, service: BrokerageServiceDep) -> BrokerageSyncOut:
    """Mirror real broker positions into holdings. The broker is authoritative."""
    return await service.sync_positions(user.id)


@router.post("/orders/{order_id}/approve", response_model=ProposedOrderOut)
async def approve_and_submit(
    order_id: uuid.UUID, user: CurrentUser, service: BrokerageServiceDep
) -> ProposedOrderOut:
    """Approve a proposed order and submit it to the linked broker.

    This spends real money. It is the only route that can, and it requires an
    explicit call per order — there is no bulk-approve endpoint by design.
    """
    return await service.approve_and_submit(user.id, order_id)


@router.post("/orders/{order_id}/refresh", response_model=ProposedOrderOut)
async def refresh_order(
    order_id: uuid.UUID, user: CurrentUser, service: BrokerageServiceDep
) -> ProposedOrderOut:
    """Poll the broker for a submitted order and settle holdings if it filled."""
    return await service.refresh_order(user.id, order_id)
