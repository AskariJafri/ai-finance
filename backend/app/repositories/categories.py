from __future__ import annotations

import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, Transaction
from app.domain.categories import SEED_CATEGORIES
from app.domain.enums import CategorySource


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, category_id: uuid.UUID) -> Category | None:
        return await self.session.get(Category, category_id)

    async def get_for_user(self, category_id: uuid.UUID, user_id: uuid.UUID) -> Category | None:
        """Fetch a category only if it's visible to this user: a system
        category, or one they own. Used everywhere a user-supplied
        ``category_id`` is about to be trusted, so one user can never point a
        transaction (or an edit/delete) at another user's category."""
        result = await self.session.execute(
            select(Category).where(
                Category.id == category_id,
                or_(Category.user_id.is_(None), Category.user_id == user_id),
            )
        )
        return result.scalar_one_or_none()

    async def get_system_by_slug(self, slug: str) -> Category | None:
        result = await self.session.execute(
            select(Category).where(Category.slug == slug, Category.user_id.is_(None))
        )
        return result.scalar_one_or_none()

    async def list_available(self, user_id: uuid.UUID) -> list[Category]:
        """System categories + this user's custom categories."""
        result = await self.session.execute(
            select(Category)
            .where(or_(Category.user_id.is_(None), Category.user_id == user_id))
            .order_by(Category.group, Category.name)
        )
        return list(result.scalars().all())

    async def list_owned(self, user_id: uuid.UUID) -> list[Category]:
        """This user's own custom categories (excludes system rows)."""
        result = await self.session.execute(
            select(Category)
            .where(Category.user_id == user_id)
            .order_by(Category.group, Category.name)
        )
        return list(result.scalars().all())

    async def slugs_taken(self, user_id: uuid.UUID) -> set[str]:
        """Every slug this user must not collide with: all system slugs plus
        their own. Used to uniquify a new category's slug — a new category can
        never shadow a system one, and that's checked here rather than relying
        on the (user_id, slug) unique constraint, which doesn't help: NULL
        user_id is SQL-distinct from every user's id, so it wouldn't stop a
        user's "groceries" from colliding with the system "groceries"."""
        available = await self.list_available(user_id)
        return {c.slug for c in available}

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        slug: str,
        group,
        color: str | None,
        budget_bucket: str | None,
    ) -> Category:
        category = Category(
            user_id=user_id,
            name=name,
            slug=slug,
            group=group,
            color=color,
            is_system=False,
            budget_bucket=budget_bucket,
        )
        self.session.add(category)
        await self.session.flush()
        return category

    async def reassign_transactions(
        self, *, from_category_id: uuid.UUID, to_category_id: uuid.UUID | None, user_id: uuid.UUID
    ) -> None:
        """Move every transaction off a category being deleted.

        Sets ``category_source`` to UNCATEGORIZED too — not just the FK — so
        the "N uncategorized" nudge (which counts by ``category_source``, not
        by ``category_id``) picks these rows up. Relying on the FK's
        ``ON DELETE SET NULL`` alone would leave them invisible to that count.
        """
        await self.session.execute(
            update(Transaction)
            .where(Transaction.category_id == from_category_id, Transaction.user_id == user_id)
            .values(
                category_id=to_category_id,
                category_source=CategorySource.UNCATEGORIZED,
                category_confidence=0.0,
            )
        )

    async def delete(self, category: Category) -> None:
        await self.session.delete(category)

    async def ensure_seed(self) -> dict[str, Category]:
        """Idempotently insert system categories; return slug -> Category map."""
        result = await self.session.execute(select(Category).where(Category.user_id.is_(None)))
        existing = {c.slug: c for c in result.scalars().all()}
        for seed in SEED_CATEGORIES:
            current = existing.get(seed.slug)
            if current is None:
                cat = Category(
                    user_id=None,
                    name=seed.name,
                    slug=seed.slug,
                    group=seed.group,
                    icon=seed.icon,
                    color=seed.color,
                    is_system=True,
                    budget_bucket=seed.budget_bucket,
                )
                self.session.add(cat)
                existing[seed.slug] = cat
            elif current.budget_bucket is None and seed.budget_bucket is not None:
                # Backfill for installs that seeded before budget_bucket existed
                # and whose migration backfill guessed 'wants' for everything.
                current.budget_bucket = seed.budget_bucket
        await self.session.flush()
        return existing
