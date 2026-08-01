"""One-time repair: re-derive ``is_transfer`` for existing transactions.

Every transaction synced before this fix went through either the dead PFC
extractor (:func:`app.integrations.plaid.real_client.extract_pfc`) or the
merchant-shadowing bug in the categorizer, so its transfer flag may be wrong.
The migration backfill (``0009_transfers_and_custom_categories``) only catches
rows already sitting in a transfer-group category; everything else needs this
script, which re-checks the persisted raw PFC and the (now-fixed) text rules,
then pair-matches the user's entire transaction history.

Never touches ``category_source == USER`` rows, and never *un-flags* a row —
it only adds ``is_transfer=True`` where a new signal justifies it. Safe to
re-run; idempotent (already-flagged/paired rows are skipped).

    docker compose exec api python -m app.scripts.rederive_transfers
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import Transaction, User
from app.db.session import SessionLocal
from app.domain.enums import CategoryGroup, CategorySource
from app.domain.normalization import normalize_merchant
from app.repositories.categories import CategoryRepository
from app.repositories.rules import CategoryRuleRepository
from app.repositories.transactions import TransactionRepository
from app.services.categorization_service import CategorizationService
from app.services.transaction_service import PAIRING_LOOKBACK_DAYS, TransactionService

logger = get_logger(__name__)

# Wide enough to cover any real transaction, without touching date.min/date.max
# (which some drivers choke on).
_FAR_PAST = date(1970, 1, 1)
_FAR_FUTURE = date(2100, 1, 1)


def _is_transfer_by_text(
    *, merchant_name: str | None, description: str | None, rules, by_id
) -> bool:
    merchant_key = normalize_merchant(merchant_name)
    desc_key = normalize_merchant(description)
    for rule in rules:
        pattern = rule.pattern
        if not pattern:
            continue
        # Mirrors CategorizationService.categorize: user corrections match only
        # the merchant key, seed rules match either field.
        keys = [merchant_key] if rule.source == "user_correction" else [merchant_key, desc_key]
        if any(pattern in k for k in keys if k):
            category = by_id.get(rule.category_id)
            return category is not None and category.group is CategoryGroup.TRANSFER
    return False


async def _flag_from_text_and_pfc(session, user_id: uuid.UUID) -> int:
    rules = await CategoryRuleRepository(session).rules_for_user(user_id)
    by_id = {c.id: c for c in await CategoryRepository(session).list_available(user_id)}

    result = await session.execute(
        select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.category_source != CategorySource.USER,
            Transaction.is_transfer.is_(False),
        )
    )
    rows = list(result.scalars().all())

    flagged = 0
    for txn in rows:
        primary = detailed = None
        if txn.raw:
            pfc = txn.raw.get("personal_finance_category") or {}
            primary = pfc.get("primary")
            detailed = pfc.get("detailed")

        is_transfer = CategorizationService._is_plaid_transfer(primary, detailed)
        if not is_transfer:
            is_transfer = _is_transfer_by_text(
                merchant_name=txn.merchant_name,
                description=txn.name,
                rules=rules,
                by_id=by_id,
            )
        if is_transfer:
            txn.is_transfer = True
            flagged += 1

    await session.flush()
    return flagged


async def _pair_match_full_history(service: TransactionService, user_id: uuid.UUID) -> int:
    date_rows = await service.session.execute(
        select(Transaction.date).where(Transaction.user_id == user_id).order_by(Transaction.date)
    )
    dates = [d for (d,) in date_rows.all()]
    if not dates:
        return 0

    window = timedelta(days=PAIRING_LOOKBACK_DAYS)
    cursor, end = dates[0], dates[-1]
    matched = 0
    while cursor <= end:
        window_end = min(cursor + window, end)
        matched += await service.match_transfers_in_range(user_id, cursor, window_end)
        cursor = window_end + timedelta(days=1)
    return matched


async def _rederive_for_user(session, user_id: uuid.UUID) -> None:
    from app.ai.factory import get_ai_categorizer

    txns = TransactionRepository(session)
    before_cents = await txns.sum_spending(user_id, _FAR_PAST, _FAR_FUTURE)

    text_flagged = await _flag_from_text_and_pfc(session, user_id)

    categorizer = CategorizationService(session, get_ai_categorizer())
    service = TransactionService(session, categorizer)
    pairs_matched = await _pair_match_full_history(service, user_id)

    after_cents = await txns.sum_spending(user_id, _FAR_PAST, _FAR_FUTURE)

    logger.info(
        "rederive_transfers user=%s text_flagged=%d pairs_matched=%d "
        "spending_before=%.2f spending_after=%.2f",
        user_id,
        text_flagged,
        pairs_matched,
        before_cents / 100,
        after_cents / 100,
    )


async def main() -> None:
    async with SessionLocal() as session:
        result = await session.execute(select(User.id))
        user_ids = [uid for (uid,) in result.all()]

        for user_id in user_ids:
            await _rederive_for_user(session, user_id)

        await session.commit()
    logger.info("rederive_transfers complete: %d users processed", len(user_ids))


if __name__ == "__main__":
    asyncio.run(main())
