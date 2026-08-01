from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, SessionDep, TransactionServiceDep
from app.repositories.categories import CategoryRepository
from app.repositories.transactions import TransactionRepository
from app.schemas.common import CategoryOut, TransactionOut, UpdateCategoryRequest

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=list[TransactionOut])
async def list_transactions(
    user: CurrentUser,
    session: SessionDep,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[TransactionOut]:
    repo = TransactionRepository(session)
    txns = await repo.list_for_user(user.id, limit=limit, offset=offset)
    return [TransactionOut.from_model(t) for t in txns]


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(user: CurrentUser, session: SessionDep) -> list[CategoryOut]:
    repo = CategoryRepository(session)
    categories = await repo.list_available(user.id)
    return [CategoryOut.from_model(c) for c in categories]


@router.patch("/{transaction_id}/category", response_model=TransactionOut)
async def update_category(
    transaction_id: uuid.UUID,
    body: UpdateCategoryRequest,
    user: CurrentUser,
    service: TransactionServiceDep,
) -> TransactionOut:
    txn = await service.recategorize_from_correction(
        user_id=user.id, txn_id=transaction_id, category_id=body.category_id
    )
    return TransactionOut.from_model(txn)
