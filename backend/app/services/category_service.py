"""User-created category CRUD.

Routers stay thin per house style: this is where the actual rules live —
which fields are mutable, what's rejected and why, and how a delete reassigns
transactions before the category itself goes away.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category
from app.domain.enums import CategoryGroup
from app.domain.slugify import slugify, uniquify
from app.repositories.categories import CategoryRepository
from app.schemas.categories import CreateCategoryRequest, UpdateCategoryDefinitionRequest
from app.services.categorization_service import CategorizationService
from app.services.errors import NotFound, ValidationFailed

_VALID_BUCKETS = {"needs", "wants"}
_DEFAULT_EXPENSE_BUCKET = "wants"
UNCATEGORIZED_SLUG = "uncategorized"


class CategoryService:
    def __init__(self, session: AsyncSession, categorizer: CategorizationService) -> None:
        self.session = session
        self.categories = CategoryRepository(session)
        self.categorizer = categorizer

    async def create(self, *, user_id: uuid.UUID, body: CreateCategoryRequest) -> Category:
        # A user-created TRANSFER category would silently rewire reported
        # income/spending and the is_transfer derivation, with no product
        # upside — the per-transaction transfer override is the right lever.
        if body.group is CategoryGroup.TRANSFER:
            raise ValidationFailed("Custom categories can't be in the transfer group.")

        budget_bucket = self._resolve_bucket(body.group, body.budget_bucket)

        # Slug is auto-generated and never exposed in the UI: uniquify against
        # ALL visible slugs (system + this user's own) so "Groceries" always
        # succeeds as "groceries-2" rather than colliding with the system seed,
        # and so a shipped seed slug can never be shadowed.
        taken = await self.categories.slugs_taken(user_id)
        slug = uniquify(slugify(body.name), taken)

        category = await self.categories.create(
            user_id=user_id,
            name=body.name,
            slug=slug,
            group=body.group,
            color=body.color,
            budget_bucket=budget_bucket,
        )
        await self.session.commit()
        await self.session.refresh(category)
        self.categorizer.invalidate()
        return category

    async def update(
        self, *, user_id: uuid.UUID, category_id: uuid.UUID, body: UpdateCategoryDefinitionRequest
    ) -> Category:
        category = await self._get_owned(category_id, user_id)

        if body.name is not None:
            category.name = body.name
        if body.color is not None:
            category.color = body.color
        if body.budget_bucket is not None:
            category.budget_bucket = self._resolve_bucket(category.group, body.budget_bucket)

        await self.session.commit()
        await self.session.refresh(category)
        self.categorizer.invalidate()
        return category

    async def delete(self, *, user_id: uuid.UUID, category_id: uuid.UUID) -> None:
        category = await self._get_owned(category_id, user_id)

        uncategorized = await self.categories.get_system_by_slug(UNCATEGORIZED_SLUG)
        await self.categories.reassign_transactions(
            from_category_id=category.id,
            to_category_id=uncategorized.id if uncategorized else None,
            user_id=user_id,
        )
        await self.categories.delete(category)
        await self.session.commit()
        self.categorizer.invalidate()

    async def _get_owned(self, category_id: uuid.UUID, user_id: uuid.UUID) -> Category:
        category = await self.categories.get_for_user(category_id, user_id)
        if category is None:
            raise NotFound("Category not found.")
        if category.is_system:
            # 404 rather than 403: built-ins aren't addressable for
            # edit/delete at all, so there's nothing to authorize against.
            raise NotFound("Category not found.")
        return category

    @staticmethod
    def _resolve_bucket(group: CategoryGroup, requested: str | None) -> str | None:
        if group is not CategoryGroup.EXPENSE:
            return None
        bucket = requested or _DEFAULT_EXPENSE_BUCKET
        if bucket not in _VALID_BUCKETS:
            raise ValidationFailed(f"budget_bucket must be one of {sorted(_VALID_BUCKETS)}.")
        return bucket
