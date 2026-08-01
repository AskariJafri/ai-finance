"""Plaid orchestration: link token, public-token exchange, and sync.

Coordinates the Plaid gateway, encrypted token storage, account upserts and the
transaction pipeline. Access tokens are encrypted before persistence and only
decrypted transiently for outbound Plaid calls.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenCipher
from app.core.logging import get_logger
from app.db.models import Account, PlaidItem
from app.integrations.plaid.factory import build_plaid_gateway, get_default_plaid_gateway
from app.integrations.plaid.gateway import PlaidGateway
from app.repositories.accounts import AccountRepository
from app.repositories.plaid_credentials import PlaidCredentialRepository
from app.repositories.plaid_items import PlaidItemRepository
from app.schemas.plaid import ExchangeResponse, LinkTokenResponse, SyncResponse
from app.services.transaction_service import ApplyResult, TransactionService

logger = get_logger(__name__)


class PlaidService:
    def __init__(
        self,
        session: AsyncSession,
        cipher: TokenCipher,
        transactions: TransactionService,
    ) -> None:
        self.session = session
        self.cipher = cipher
        self.transactions = transactions
        self.items = PlaidItemRepository(session)
        self.accounts = AccountRepository(session)
        self.creds = PlaidCredentialRepository(session)

    async def _gateway_for(self, user_id: uuid.UUID) -> PlaidGateway:
        """Prefer the user's own Plaid keys; fall back to env/mock default."""
        cred = await self.creds.get_for_user(user_id)
        if cred is not None:
            return build_plaid_gateway(
                client_id=cred.client_id,
                secret=self.cipher.decrypt(cred.secret_encrypted),
                plaid_env=cred.plaid_env,
            )
        return get_default_plaid_gateway()

    async def create_link_token(self, user_id: uuid.UUID) -> LinkTokenResponse:
        gateway = await self._gateway_for(user_id)
        link_token, expiration = await gateway.create_link_token(str(user_id))
        return LinkTokenResponse(link_token=link_token, expiration=expiration)

    async def exchange_public_token(
        self,
        *,
        user_id: uuid.UUID,
        public_token: str,
        institution_id: str | None,
        institution_name: str | None,
    ) -> ExchangeResponse:
        gateway = await self._gateway_for(user_id)
        exchange = await gateway.exchange_public_token(public_token)
        # Single-connection policy: linking a bank REPLACES whatever was linked
        # before (user's choice). Plaid also issues fresh item/account ids on
        # every link, so stacking items would double-count everything.
        replaced = await self.items.delete_all_for_user(user_id)
        if replaced:
            logger.info("Replaced %d existing bank link(s)", replaced)
        item = await self.items.create(
            user_id=user_id,
            plaid_item_id=exchange.item_id,
            access_token_encrypted=self.cipher.encrypt(exchange.access_token),
            institution_id=institution_id or exchange.institution_id,
            institution_name=institution_name or exchange.institution_name,
        )
        accounts_synced, apply = await self._sync_item(
            user_id, item, exchange.access_token, gateway
        )
        await self.session.commit()
        return ExchangeResponse(
            item_id=item.plaid_item_id,
            institution_name=item.institution_name,
            accounts_synced=accounts_synced,
            transactions_synced=apply.added + apply.modified,
        )

    async def sync_all(self, user_id: uuid.UUID) -> SyncResponse:
        totals = ApplyResult()
        accounts_synced = 0
        gateway = await self._gateway_for(user_id)
        for item in await self.items.list_for_user(user_id):
            token = self.cipher.decrypt(item.access_token_encrypted)
            count, apply = await self._sync_item(user_id, item, token, gateway)
            accounts_synced += count
            totals.added += apply.added
            totals.modified += apply.modified
            totals.removed += apply.removed
            totals.categorized_by_ai += apply.categorized_by_ai
        await self.session.commit()
        return SyncResponse(
            added=totals.added,
            modified=totals.modified,
            removed=totals.removed,
            accounts_synced=accounts_synced,
            categorized_by_ai=totals.categorized_by_ai,
        )

    async def _sync_item(
        self,
        user_id: uuid.UUID,
        item: PlaidItem,
        access_token: str,
        gateway: PlaidGateway,
    ) -> tuple[int, ApplyResult]:
        # 1. Accounts + balances.
        plaid_accounts = await gateway.get_accounts(access_token)
        accounts_by_plaid_id: dict[str, Account] = {}
        for pa in plaid_accounts:
            account = await self.accounts.upsert(
                user_id=user_id,
                plaid_item_id=item.id,
                plaid_account_id=pa.account_id,
                name=pa.name,
                official_name=pa.official_name,
                mask=pa.mask,
                type=pa.type,
                subtype=pa.subtype,
                currency=pa.currency,
                current_balance_cents=pa.current_balance_cents,
                available_balance_cents=pa.available_balance_cents,
            )
            accounts_by_plaid_id[pa.account_id] = account

        # 2. Incremental transactions/sync (paginate until caught up).
        cursor = item.transactions_cursor
        added, modified, removed = [], [], []
        while True:
            page = await gateway.sync_transactions(access_token, cursor)
            added.extend(page.added)
            modified.extend(page.modified)
            removed.extend(page.removed_ids)
            cursor = page.next_cursor
            if not page.has_more:
                break
        await self.items.set_cursor(item, cursor)

        apply = await self.transactions.apply_sync(
            user_id=user_id,
            added=added,
            modified=modified,
            removed_ids=removed,
            accounts_by_plaid_id=accounts_by_plaid_id,
        )
        return len(plaid_accounts), apply
