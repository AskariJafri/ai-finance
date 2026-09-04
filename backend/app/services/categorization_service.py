"""Transaction categorization pipeline.

Order of precedence (first hit wins):
  1. Rules — user correction rules (high priority) then shipped seed rules.
  2. AI fallback — the LLM picks from the known category slugs for merchants no
     rule covers.
  3. Uncategorized — if the AI is unavailable or unsure.

Rules and the category map are loaded once per service instance and cached, so
categorizing a whole sync batch issues just two queries up front rather than
two per transaction. Every rule hit bumps ``hit_count`` for later confidence
tuning.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.categorizer import AICategorizer
from app.db.models import Category, CategoryRule
from app.domain.enums import CategoryGroup, CategorySource
from app.domain.normalization import normalize_merchant
from app.repositories.categories import CategoryRepository
from app.repositories.rules import CategoryRuleRepository

RULE_CONFIDENCE_USER = 1.0
RULE_CONFIDENCE_SEED = 0.95
RULE_CONFIDENCE_PLAID = 0.99

# Plaid personal_finance_category values that are movements between the user's
# own accounts or debt repayments — NOT income and NOT spending. Catching these
# keeps both legs of a credit-card payment (the outflow from chequing and the
# matching inflow on the card) out of the inbound/outbound totals.
_TRANSFER_PFC_PRIMARY = {"TRANSFER_IN", "TRANSFER_OUT"}
_TRANSFER_PFC_DETAILED_SUBSTR = "CREDIT_CARD_PAYMENT"

TRANSFER_SLUG = "transfer"
CREDIT_CARD_PAYMENT_SLUG = "credit-card-payment"


@dataclass(frozen=True)
class CategorizationResult:
    category_id: uuid.UUID | None
    source: CategorySource
    confidence: float
    # Whether this row is a value-neutral movement between the user's own
    # accounts. Decided here, where the chosen category's group is already
    # known, so the write path has one flag to persist and no logic of its own.
    is_transfer: bool = False


class CategorizationService:
    def __init__(self, session: AsyncSession, ai: AICategorizer) -> None:
        self.session = session
        self.ai = ai
        self.categories = CategoryRepository(session)
        self.rules_repo = CategoryRuleRepository(session)
        self._rules: list[CategoryRule] | None = None
        self._by_slug: dict[str, Category] | None = None
        self._by_id: dict[uuid.UUID, Category] | None = None
        self._slugs: list[str] | None = None
        # Merchant key -> the AI's answer, for this request only.
        #
        # The AI branch is one metered request per transaction, and a sync is
        # mostly repeats: two Starbucks charges in one batch used to be two
        # requests. On a free tier metered at ~50 requests a DAY, a 60-row sync
        # could spend the whole allowance before the user opened Invest once —
        # which is what made the AI look "offline" by lunchtime.
        #
        # Request-scoped because the service is built per request
        # (api/deps.py get_categorization_service), so it is bounded by one
        # batch and needs no eviction. It is not a cache of AI output across
        # requests: the next sync asks again.
        self._ai_memo: dict[str, tuple[str | None, float]] = {}

    async def _load(self, user_id: uuid.UUID) -> None:
        if self._rules is None:
            self._rules = await self.rules_repo.rules_for_user(user_id)
        if self._by_slug is None:
            available = await self.categories.list_available(user_id)
            # Built in two passes so a system slug can never be shadowed by a
            # user category that collides with it. ``list_available`` orders by
            # (group, name), which says nothing about which row wins a dict
            # comprehension — and the (user_id, slug) unique constraint doesn't
            # help because NULL user_id is distinct from every user's id.
            # Slug creation also uniquifies against system slugs; this is the
            # second line of defence for rows that predate that.
            self._by_slug = {c.slug: c for c in available if not c.is_system}
            self._by_slug.update({c.slug: c for c in available if c.is_system})
            self._by_id = {c.id: c for c in available}
            self._slugs = list(self._by_slug.keys())

    def invalidate(self) -> None:
        """Call after mutating rules/categories so caches reload next use."""
        self._rules = None
        self._by_slug = None
        self._by_id = None
        self._slugs = None
        # The memo is keyed on the merchant, but the *answer* is a slug from
        # the category set that just changed — so it goes with the rest.
        self._ai_memo = {}

    @staticmethod
    def _is_plaid_transfer(primary: str | None, detailed: str | None) -> bool:
        if (primary or "").upper() in _TRANSFER_PFC_PRIMARY:
            return True
        return _TRANSFER_PFC_DETAILED_SUBSTR in (detailed or "").upper()

    def _is_transfer_category(self, category_id: uuid.UUID | None) -> bool:
        """Transfer-ness of a chosen category, for the rule/AI branches."""
        if category_id is None or self._by_id is None:
            return False
        category = self._by_id.get(category_id)
        return category is not None and category.group is CategoryGroup.TRANSFER

    def available_for_prompt(self) -> list[tuple[str, str]]:
        """``[(slug, display name)]`` for the AI prompt.

        Auto-uniquified slugs like ``groceries-2`` mean nothing to the model on
        their own, so it is shown the name alongside. The value it returns is
        still validated against the bare slug list.
        """
        if self._by_slug is None:
            return []
        return [(slug, category.name) for slug, category in self._by_slug.items()]

    async def categorize(
        self,
        *,
        user_id: uuid.UUID,
        description: str,
        merchant_name: str | None,
        plaid_category: str | None = None,
        plaid_category_detailed: str | None = None,
    ) -> CategorizationResult:
        await self._load(user_id)
        assert self._rules is not None and self._by_slug is not None and self._slugs is not None

        # Two keys, not one. The old single key was
        # ``merchant(merchant_name) or merchant(description)``, so a non-empty
        # merchant_name *hid* the description entirely — and Plaid sends
        # merchant_name="Chase" with name="PAYMENT - THANK YOU ... CARD ENDING
        # 1234", i.e. the useless field shadows the one that identifies a card
        # payment. Card-payment text rules could therefore never fire.
        merchant_key = normalize_merchant(merchant_name)
        desc_key = normalize_merchant(description)
        # User corrections are STORED keyed on the merchant
        # (TransactionService.recategorize_from_correction), so they must be
        # matched the same way — widening them to the description would make
        # their already-broad over-generalization strictly worse.
        user_keys = [merchant_key] if merchant_key else ([desc_key] if desc_key else [])
        # Seed rules are keyword patterns, so either field may match.
        seed_keys = [k for k in (merchant_key, desc_key) if k]

        # 1. User-correction rules always win (the learning loop).
        for rule in self._rules:
            if (
                rule.source == "user_correction"
                and rule.pattern
                and any(rule.pattern in k for k in user_keys)
            ):
                await self.rules_repo.increment_hit(rule)
                return CategorizationResult(
                    rule.category_id,
                    CategorySource.RULE,
                    RULE_CONFIDENCE_USER,
                    is_transfer=self._is_transfer_category(rule.category_id),
                )

        # 2. Plaid's own transfer / credit-card-payment classification. This is
        #    text-independent, so it catches card payments the merchant rules
        #    would miss — keeping them out of both income and spending.
        if self._is_plaid_transfer(plaid_category, plaid_category_detailed):
            is_card_payment = (
                _TRANSFER_PFC_DETAILED_SUBSTR in (plaid_category_detailed or "").upper()
            )
            slug = CREDIT_CARD_PAYMENT_SLUG if is_card_payment else TRANSFER_SLUG
            category = self._by_slug.get(slug) or self._by_slug.get(TRANSFER_SLUG)
            if category is not None:
                return CategorizationResult(
                    category.id,
                    CategorySource.PLAID,
                    RULE_CONFIDENCE_PLAID,
                    is_transfer=True,
                )

        # 3. Shipped seed rules.
        for rule in self._rules:
            if (
                rule.source != "user_correction"
                and rule.pattern
                and any(rule.pattern in k for k in seed_keys)
            ):
                await self.rules_repo.increment_hit(rule)
                return CategorizationResult(
                    rule.category_id,
                    CategorySource.RULE,
                    RULE_CONFIDENCE_SEED,
                    is_transfer=self._is_transfer_category(rule.category_id),
                )

        # 4. AI fallback, memoized per merchant for the life of this request.
        #
        # The miss is remembered too. An unrecognizable merchant returns
        # (None, 0.0), and without memoizing that, the one payee the model
        # cannot place would be re-asked on every row it appears in — the worst
        # case rather than the best one.
        memo_key = merchant_key or desc_key
        if memo_key and memo_key in self._ai_memo:
            slug, confidence = self._ai_memo[memo_key]
        else:
            slug, confidence = await self.ai.categorize(
                description=description,
                merchant_name=merchant_name,
                category_slugs=self._slugs,
                category_names=self.available_for_prompt(),
            )
            if memo_key:
                self._ai_memo[memo_key] = (slug, confidence)
        if slug and slug in self._by_slug and slug != "uncategorized":
            chosen = self._by_slug[slug]
            return CategorizationResult(
                chosen.id,
                CategorySource.AI,
                confidence,
                is_transfer=chosen.group is CategoryGroup.TRANSFER,
            )

        # 5. Uncategorized. is_transfer stays False: an unknown outflow counts as
        #    spending rather than vanishing from the totals. Failing open toward
        #    spending is deliberate (it over-reports rather than under-reports,
        #    and nudges the user to categorize) and is pinned by a test.
        fallback = self._by_slug.get("uncategorized")
        return CategorizationResult(
            fallback.id if fallback else None, CategorySource.UNCATEGORIZED, 0.0
        )
