from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CategoryRule
from app.domain.categories import SEED_RULES
from app.domain.enums import RuleMatchType
from app.repositories.categories import CategoryRepository

# User corrections must always win over shipped seed rules.
SEED_PRIORITY = 100
USER_CORRECTION_PRIORITY = 10


class CategoryRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def rules_for_user(self, user_id: uuid.UUID) -> list[CategoryRule]:
        """Global seed rules + this user's correction rules, best priority first."""
        result = await self.session.execute(
            select(CategoryRule)
            .where((CategoryRule.user_id.is_(None)) | (CategoryRule.user_id == user_id))
            .order_by(CategoryRule.priority.asc())
        )
        return list(result.scalars().all())

    async def upsert_user_correction(
        self, *, user_id: uuid.UUID, pattern: str, category_id: uuid.UUID
    ) -> CategoryRule:
        """Record/strengthen a user correction as a high-priority merchant rule."""
        result = await self.session.execute(
            select(CategoryRule).where(
                CategoryRule.user_id == user_id,
                CategoryRule.match_type == RuleMatchType.MERCHANT,
                CategoryRule.pattern == pattern,
            )
        )
        rule = result.scalar_one_or_none()
        if rule is None:
            rule = CategoryRule(
                user_id=user_id,
                match_type=RuleMatchType.MERCHANT,
                pattern=pattern,
                category_id=category_id,
                source="user_correction",
                priority=USER_CORRECTION_PRIORITY,
                hit_count=1,
            )
            self.session.add(rule)
        else:
            rule.category_id = category_id
            rule.hit_count += 1
        await self.session.flush()
        return rule

    async def increment_hit(self, rule: CategoryRule) -> None:
        rule.hit_count += 1
        await self.session.flush()

    async def ensure_seed(self) -> None:
        """Idempotently insert global seed rules mapping merchant keys to categories."""
        categories = CategoryRepository(self.session)
        slug_map = await categories.ensure_seed()

        result = await self.session.execute(
            select(CategoryRule.pattern).where(
                CategoryRule.user_id.is_(None),
                CategoryRule.match_type == RuleMatchType.MERCHANT,
            )
        )
        existing = set(result.scalars().all())
        for pattern, slug in SEED_RULES.items():
            category = slug_map.get(slug)
            if category is None or pattern in existing:
                continue
            self.session.add(
                CategoryRule(
                    user_id=None,
                    match_type=RuleMatchType.MERCHANT,
                    pattern=pattern,
                    category_id=category.id,
                    source="seed",
                    priority=SEED_PRIORITY,
                )
            )
        await self.session.flush()
