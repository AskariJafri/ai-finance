from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CategoryServiceDep, CurrentUser, SessionDep
from app.repositories.categories import CategoryRepository
from app.schemas.categories import CreateCategoryRequest, UpdateCategoryDefinitionRequest
from app.schemas.common import CategoryOut

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
async def list_categories(user: CurrentUser, session: SessionDep) -> list[CategoryOut]:
    repo = CategoryRepository(session)
    categories = await repo.list_available(user.id)
    return [CategoryOut.from_model(c) for c in categories]


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CreateCategoryRequest, user: CurrentUser, service: CategoryServiceDep
) -> CategoryOut:
    category = await service.create(user_id=user.id, body=body)
    return CategoryOut.from_model(category)


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: uuid.UUID,
    body: UpdateCategoryDefinitionRequest,
    user: CurrentUser,
    service: CategoryServiceDep,
) -> CategoryOut:
    category = await service.update(user_id=user.id, category_id=category_id, body=body)
    return CategoryOut.from_model(category)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID, user: CurrentUser, service: CategoryServiceDep
) -> None:
    await service.delete(user_id=user.id, category_id=category_id)
