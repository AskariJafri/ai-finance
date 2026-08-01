"""Real Plaid adapter wrapping the synchronous ``plaid-python`` SDK.

The SDK is blocking, so every call is offloaded to a thread via
``asyncio.to_thread`` to avoid stalling the event loop. Plaid types are
imported lazily so environments that only use the mock client (e.g. CI unit
tests) don't need the SDK importable.
"""

from __future__ import annotations

import asyncio
from datetime import date

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.plaid.gateway import (
    PlaidAccount,
    PlaidExchangeResult,
    PlaidGateway,
    PlaidSyncResult,
    PlaidTransaction,
)

logger = get_logger(__name__)


def _to_cents(amount: float | None) -> int:
    return int(round((amount or 0.0) * 100))


def extract_pfc(raw) -> tuple[str | None, str | None]:
    """Pull ``(primary, detailed)`` out of a transaction's ``personal_finance_category``.

    This is a module-level function (not a closure inside ``sync_transactions``)
    so it can be tested without importing the Plaid SDK — the lack of coverage at
    this boundary is exactly why the previous version shipped broken.

    That version gated on ``isinstance(pfc, dict)``, which is **always False**:
    on plaid-python 42.x ``PersonalFinanceCategory`` is a ``ModelNormal``, not a
    dict subclass. Every PFC value was silently dropped against real bank data,
    while the tests passed because they inject plain strings further downstream.

    Accessor order is deliberate: ``to_dict()`` first (canonical, returns a plain
    dict), ``getattr`` with a default second (``ModelNormal`` raises on *unset*
    optional attributes, so the default matters), and ``dict.get`` for the mock /
    plain-dict path. Never gate on the concrete type again.
    """
    pfc = raw.get("personal_finance_category") if hasattr(raw, "get") else None
    if pfc is None:
        return None, None

    to_dict = getattr(pfc, "to_dict", None)
    if callable(to_dict):
        try:
            pfc = to_dict()
        except Exception:  # pragma: no cover - defensive against SDK changes
            pass

    def field(name: str) -> str | None:
        if isinstance(pfc, dict):
            value = pfc.get(name)
        else:
            value = getattr(pfc, name, None)
        return str(value) if value is not None else None

    return field("primary"), field("detailed")


class RealPlaidClient(PlaidGateway):
    def __init__(
        self,
        client_id: str | None = None,
        secret: str | None = None,
        plaid_env: str | None = None,
    ) -> None:
        import plaid
        from plaid.api import plaid_api

        host_map = {
            "sandbox": plaid.Environment.Sandbox,
            "production": plaid.Environment.Production,
        }
        env = plaid_env or settings.plaid_env
        # 'development' was retired by Plaid; fall back to sandbox host.
        host = host_map.get(env, plaid.Environment.Sandbox)
        configuration = plaid.Configuration(
            host=host,
            api_key={
                "clientId": client_id or settings.plaid_client_id,
                "secret": secret or settings.plaid_secret,
            },
        )
        self._client = plaid_api.PlaidApi(plaid.ApiClient(configuration))

    async def create_link_token(self, user_id: str) -> tuple[str, str | None]:
        from plaid.exceptions import ApiException
        from plaid.model.country_code import CountryCode
        from plaid.model.link_token_create_request import LinkTokenCreateRequest
        from plaid.model.link_token_create_request_user import (
            LinkTokenCreateRequestUser,
        )
        from plaid.model.products import Products

        def _build(with_android_package: bool) -> LinkTokenCreateRequest:
            req = LinkTokenCreateRequest(
                user=LinkTokenCreateRequestUser(client_user_id=user_id),
                client_name=settings.app_name,
                products=[Products(p) for p in settings.plaid_product_list],
                country_codes=[CountryCode(c) for c in settings.plaid_country_code_list],
                language="en",
            )
            if settings.plaid_redirect_uri:
                req.redirect_uri = settings.plaid_redirect_uri
            # OAuth banks on Android return to the app via the registered package name.
            if with_android_package and settings.plaid_android_package_name:
                req.android_package_name = settings.plaid_android_package_name
            return req

        try:
            response = await asyncio.to_thread(
                self._client.link_token_create, _build(with_android_package=True)
            )
        except ApiException as exc:
            # The Android package must be registered in the Plaid Dashboard. If it
            # isn't, Plaid rejects the request — fall back to a token without it so
            # Link still opens (OAuth banks then need that Dashboard registration).
            if "android_package_name" in (exc.body or ""):
                logger.warning(
                    "Plaid rejected android_package_name=%s; retrying without it. "
                    "Register it in the Plaid Dashboard to enable OAuth banks (e.g. TD).",
                    settings.plaid_android_package_name,
                )
                response = await asyncio.to_thread(
                    self._client.link_token_create, _build(with_android_package=False)
                )
            else:
                raise

        expiration = response.get("expiration")
        return response["link_token"], (str(expiration) if expiration else None)

    async def exchange_public_token(self, public_token: str) -> PlaidExchangeResult:
        from plaid.model.item_public_token_exchange_request import (
            ItemPublicTokenExchangeRequest,
        )

        response = await asyncio.to_thread(
            self._client.item_public_token_exchange,
            ItemPublicTokenExchangeRequest(public_token=public_token),
        )
        return PlaidExchangeResult(
            access_token=response["access_token"],
            item_id=response["item_id"],
        )

    async def get_accounts(self, access_token: str) -> list[PlaidAccount]:
        from plaid.model.accounts_get_request import AccountsGetRequest

        response = await asyncio.to_thread(
            self._client.accounts_get, AccountsGetRequest(access_token=access_token)
        )
        accounts: list[PlaidAccount] = []
        for acc in response["accounts"]:
            balances = acc["balances"]
            accounts.append(
                PlaidAccount(
                    account_id=acc["account_id"],
                    name=acc["name"],
                    official_name=acc.get("official_name"),
                    mask=acc.get("mask"),
                    type=str(acc["type"]) if acc.get("type") else None,
                    subtype=str(acc["subtype"]) if acc.get("subtype") else None,
                    currency=balances.get("iso_currency_code") or "USD",
                    current_balance_cents=_to_cents(balances.get("current")),
                    available_balance_cents=(
                        _to_cents(balances.get("available"))
                        if balances.get("available") is not None
                        else None
                    ),
                )
            )
        return accounts

    async def sync_transactions(self, access_token: str, cursor: str | None) -> PlaidSyncResult:
        from plaid.model.transactions_sync_request import TransactionsSyncRequest

        request = TransactionsSyncRequest(access_token=access_token, cursor=cursor or "")
        response = await asyncio.to_thread(self._client.transactions_sync, request)

        def _map(raw: dict) -> PlaidTransaction:
            raw_date = raw["date"]
            txn_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
            plaid_category, plaid_category_detailed = extract_pfc(raw)
            return PlaidTransaction(
                transaction_id=raw["transaction_id"],
                account_id=raw["account_id"],
                name=raw.get("name") or "",
                merchant_name=raw.get("merchant_name"),
                amount_cents=_to_cents(raw.get("amount")),
                currency=raw.get("iso_currency_code") or "USD",
                date=txn_date,
                pending=bool(raw.get("pending", False)),
                plaid_category=plaid_category,
                plaid_category_detailed=plaid_category_detailed,
            )

        return PlaidSyncResult(
            added=[_map(t) for t in response["added"]],
            modified=[_map(t) for t in response["modified"]],
            removed_ids=[t["transaction_id"] for t in response["removed"]],
            next_cursor=response.get("next_cursor"),
            has_more=bool(response.get("has_more", False)),
        )
