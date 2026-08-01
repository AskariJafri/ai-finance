"""Transaction persistence + the categorization/learning loop.

``apply_sync`` turns a normalized Plaid sync batch into rows: it dedupes by
Plaid transaction id (idempotent re-syncs), resolves/creates the merchant,
categorizes via :class:`CategorizationService`, and preserves any category the
user set manually. ``recategorize_from_correction`` records a user override as
a high-priority rule so future transactions from that merchant are auto-tagged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Transaction
from app.domain.accounts import is_liability
from app.domain.enums import CategoryGroup, CategorySource
from app.domain.normalization import normalize_merchant
from app.domain.transfer_matching import DEFAULT_MAX_DAY_GAP, find_pairs
from app.integrations.plaid.gateway import PlaidTransaction
from app.repositories.accounts import AccountRepository
from app.repositories.categories import CategoryRepository
from app.repositories.merchants import MerchantRepository
from app.repositories.rules import CategoryRuleRepository
from app.repositories.transactions import TransactionRepository
from app.services.categorization_service import (
    CREDIT_CARD_PAYMENT_SLUG,
    TRANSFER_SLUG,
    CategorizationService,
)
from app.services.errors import NotFound

# How far back the pair matcher looks on every sync. Leg B of a transfer routinely
# arrives in a *later* sync than leg A (pending -> posted), so re-scanning recent
# history is what actually catches card payments, not just scanning the new rows.
PAIRING_LOOKBACK_DAYS = 45


@dataclass
class ApplyResult:
    added: int = 0
    modified: int = 0
    removed: int = 0
    categorized_by_ai: int = 0
    transfer_pairs_matched: int = 0


class TransactionService:
    def __init__(self, session: AsyncSession, categorizer: CategorizationService) -> None:
        self.session = session
        self.categorizer = categorizer
        self.txns = TransactionRepository(session)
        self.merchants = MerchantRepository(session)
        self.rules = CategoryRuleRepository(session)
        self.categories = CategoryRepository(session)
        self.accounts = AccountRepository(session)

    async def apply_sync(
        self,
        *,
        user_id: uuid.UUID,
        added: list[PlaidTransaction],
        modified: list[PlaidTransaction],
        removed_ids: list[str],
        accounts_by_plaid_id: dict[str, Account],
    ) -> ApplyResult:
        result = ApplyResult()

        for ptxn in added:
            if await self._upsert(user_id, ptxn, accounts_by_plaid_id, result):
                result.added += 1
        for ptxn in modified:
            if await self._upsert(user_id, ptxn, accounts_by_plaid_id, result):
                result.modified += 1
        for plaid_id in removed_ids:
            await self.txns.delete_by_plaid_id(plaid_id)
            result.removed += 1

        end = date.today()
        start = end - timedelta(days=PAIRING_LOOKBACK_DAYS)
        result.transfer_pairs_matched = await self.match_transfers_in_range(user_id, start, end)
        return result

    async def match_transfers_in_range(self, user_id: uuid.UUID, start: date, end: date) -> int:
        """Pair-match transactions in ``[start, end]`` and flag both legs of each match.

        Called from ``apply_sync`` with a trailing :data:`PAIRING_LOOKBACK_DAYS`
        window on *every* sync — not just the rows just added — because the
        outflow leg of a card payment routinely posts before the inflow leg (or
        vice versa), so leg B often only shows up in a later sync.

        Also called directly by ``app.scripts.rederive_transfers`` with
        arbitrary historical windows, for the one-time full-history repair.
        Idempotent either way: already-paired legs are excluded from
        ``pairing_candidates`` via ``has_group``, so re-running finds nothing
        new for rows already matched.
        """
        legs = await self.txns.pairing_candidates(user_id, start, end)
        if not legs:
            return 0

        pairs = find_pairs(legs, max_day_gap=DEFAULT_MAX_DAY_GAP)
        if not pairs:
            return 0

        legs_by_id = {leg.id: leg for leg in legs}
        accounts_by_id = {a.id: a for a in await self.accounts.list_for_user(user_id)}

        transfer_category = await self.categories.get_system_by_slug(TRANSFER_SLUG)
        card_payment_category = await self.categories.get_system_by_slug(CREDIT_CARD_PAYMENT_SLUG)

        for outflow_id, inflow_id in pairs:
            out_leg = legs_by_id[outflow_id]
            in_leg = legs_by_id[inflow_id]
            out_account = accounts_by_id.get(out_leg.account_id)
            in_account = accounts_by_id.get(in_leg.account_id)
            either_liability = is_liability(getattr(out_account, "type", None)) or is_liability(
                getattr(in_account, "type", None)
            )
            category = card_payment_category if either_liability else transfer_category
            await self.txns.mark_transfer_pair(
                outflow_id=outflow_id,
                inflow_id=inflow_id,
                group_id=uuid.uuid4(),
                category_id=category.id if category else None,
            )

        return len(pairs)

    async def _upsert(
        self,
        user_id: uuid.UUID,
        ptxn: PlaidTransaction,
        accounts_by_plaid_id: dict[str, Account],
        result: ApplyResult,
    ) -> bool:
        account = accounts_by_plaid_id.get(ptxn.account_id)
        if account is None:
            return False  # transaction for an account we didn't sync; skip

        existing = await self.txns.get_by_plaid_id(ptxn.transaction_id)

        # Resolve merchant for display + future matching.
        normalized = normalize_merchant(ptxn.merchant_name) or normalize_merchant(ptxn.name)
        merchant = None
        if normalized:
            merchant = await self.merchants.get_or_create(
                normalized_name=normalized,
                display_name=ptxn.merchant_name or ptxn.name,
            )

        txn = existing or Transaction(
            user_id=user_id,
            account_id=account.id,
            plaid_transaction_id=ptxn.transaction_id,
        )
        txn.account_id = account.id
        txn.merchant_id = merchant.id if merchant else None
        txn.name = ptxn.name
        txn.merchant_name = ptxn.merchant_name
        txn.amount_cents = ptxn.amount_cents
        txn.currency = ptxn.currency
        txn.date = ptxn.date
        txn.pending = ptxn.pending
        # Persisted so is_transfer can be re-derived later (e.g. rederive_transfers)
        # without a full Plaid re-sync.
        if ptxn.plaid_category or ptxn.plaid_category_detailed:
            txn.raw = {
                "personal_finance_category": {
                    "primary": ptxn.plaid_category,
                    "detailed": ptxn.plaid_category_detailed,
                }
            }

        # Never clobber a user's manual categorization on re-sync.
        if existing is None or existing.category_source != CategorySource.USER:
            categorization = await self.categorizer.categorize(
                user_id=user_id,
                description=ptxn.name,
                merchant_name=ptxn.merchant_name,
                plaid_category=ptxn.plaid_category,
                plaid_category_detailed=ptxn.plaid_category_detailed,
            )
            txn.category_id = categorization.category_id
            txn.category_source = categorization.source
            txn.category_confidence = categorization.confidence
            txn.is_transfer = categorization.is_transfer
            if categorization.source == CategorySource.AI:
                result.categorized_by_ai += 1

        if existing is None:
            await self.txns.add(txn)
        return True

    async def recategorize_from_correction(
        self, *, user_id: uuid.UUID, txn_id: uuid.UUID, category_id: uuid.UUID
    ) -> Transaction:
        txn = await self.txns.get_by_id(txn_id)
        if txn is None or txn.user_id != user_id:
            raise NotFound("Transaction not found.")

        # Validate the category belongs to this user (or is a system category)
        # before trusting it — otherwise a user could point their transaction at
        # another user's custom category.
        category = await self.categories.get_for_user(category_id, user_id)
        if category is None:
            raise NotFound("Category not found.")

        txn.category_id = category_id
        txn.category_source = CategorySource.USER
        txn.category_confidence = 1.0
        is_transfer = category.group is CategoryGroup.TRANSFER
        txn.is_transfer = is_transfer

        # If this leg was already paired, keep the pair's flag in sync — a user
        # correcting one leg out of (or into) "transfer" should apply to both.
        if txn.transfer_group_id is not None:
            partner = await self.txns.get_transfer_partner(txn.transfer_group_id, txn.id)
            if partner is not None:
                partner.is_transfer = is_transfer

        # Learning loop: persist a high-priority rule keyed on the merchant —
        # but NOT for a transfer-group target. Transfer-ness is a property of the
        # account pair and direction, not the merchant: a rule like "CHASE" ->
        # credit-card-payment would silently turn every other Chase transaction
        # into a transfer too. There's nothing legitimate to learn here.
        if not is_transfer:
            normalized = normalize_merchant(txn.merchant_name) or normalize_merchant(txn.name)
            if normalized:
                await self.rules.upsert_user_correction(
                    user_id=user_id, pattern=normalized, category_id=category_id
                )
                self.categorizer.invalidate()

        await self.session.commit()
        # Eager-load relationships for serialization (async has no implicit
        # lazy-load) — TransactionOut reads both category and account.
        await self.session.refresh(txn, ["category", "account"])
        return txn
