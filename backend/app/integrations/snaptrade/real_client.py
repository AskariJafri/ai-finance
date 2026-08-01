"""Real SnapTrade adapter.

The ``snaptrade_client`` SDK is synchronous, so every call is wrapped in
``asyncio.to_thread`` to avoid stalling the event loop — same approach as
``RealPlaidClient``. SDK responses are dict-like bodies; we read them
defensively and map into the frozen DTOs in ``gateway`` so no vendor type
escapes this module.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.snaptrade.gateway import (
    BrokerOrderState,
    OrderAction,
    OrderType,
    SnapTradeAccount,
    SnapTradeError,
    SnapTradeGateway,
    SnapTradeOrderResult,
    SnapTradePosition,
    SnapTradeRegistration,
    SnapTradeSymbol,
)

logger = get_logger(__name__)

# SnapTrade exposes many broker-specific order states; collapse them onto the
# five the app models. Anything unrecognized is treated as still-open
# ("submitted") so a poll retries rather than declaring a false terminal state.
_TERMINAL_STATES: dict[str, BrokerOrderState] = {
    "EXECUTED": "filled",
    "FILLED": "filled",
    "REJECTED": "rejected",
    "FAILED": "failed",
    "CANCELED": "cancelled",
    "CANCELLED": "cancelled",
    "EXPIRED": "cancelled",
}

# Exchange code -> Yahoo ticker suffix, so broker symbols join against
# ``Holding.symbol`` and the market data provider.
_EXCHANGE_SUFFIX = {"TSX": ".TO", "TSXV": ".V", "CSE": ".CN", "NEO": ".NE"}

# Personal SnapTrade keys (clientId prefix "PERS-") authenticate via the signed
# consumerKey alone: there is no ``registerUser`` and no per-user secret — you
# omit userId/userSecret on every request (the key *is* the identity). The
# service layer still expects a (user_id, secret) pair to store and thread
# through, so registration returns this sentinel; it is never sent to SnapTrade
# (see ``RealSnapTradeClient._auth``).
_PERSONAL_SENTINEL = "personal-key-user"


def _to_cents(amount: Any) -> int:
    """Money -> integer cents. ``None``/unparseable becomes 0."""
    if amount is None:
        return 0
    try:
        return int(round(float(amount) * 100))
    except (TypeError, ValueError):
        return 0


def _body(response: Any) -> Any:
    """SDK responses expose ``.body``; tolerate plain dicts/lists too."""
    return getattr(response, "body", response)


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Read the first present key from a dict-like SDK object."""
    if obj is None:
        return default
    for key in keys:
        if isinstance(obj, dict):
            if key in obj and obj[key] is not None:
                return obj[key]
        else:
            value = getattr(obj, key, None)
            if value is not None:
                return value
    return default


def _normalize_ticker(symbol_obj: Any) -> str:
    """Broker symbol payload -> Yahoo-style ticker (``VFV.TO``)."""
    raw = _get(symbol_obj, "symbol", "raw_symbol", "ticker", default="") or ""
    ticker = str(raw).upper().strip()
    if "." in ticker:
        return ticker
    exchange = _get(symbol_obj, "exchange", default={})
    code = str(_get(exchange, "code", "mic_code", default="") or "").upper()
    return f"{ticker}{_EXCHANGE_SUFFIX.get(code, '')}"


def _map_order(raw: Any) -> SnapTradeOrderResult:
    status = str(_get(raw, "status", "state", default="") or "").upper()
    fill_price = _get(raw, "execution_price", "average_fill_price", "price")
    return SnapTradeOrderResult(
        broker_order_id=_get(raw, "brokerage_order_id", "id"),
        state=_TERMINAL_STATES.get(status, "submitted"),
        filled_qty=_get(raw, "filled_quantity", "total_quantity"),
        fill_price_cents=_to_cents(fill_price) if fill_price is not None else None,
        detail=status or None,
        updated_at=datetime.now(UTC),
    )


class RealSnapTradeClient(SnapTradeGateway):
    def __init__(self, *, client_id: str | None = None, consumer_key: str | None = None) -> None:
        try:
            from snaptrade_client import SnapTrade, SnapTradeAuth
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise SnapTradeError(
                "snaptrade-python-sdk is not installed; "
                "set SNAPTRADE_USE_MOCK=true or install the dependency"
            ) from exc

        cid = client_id or settings.snaptrade_client_id
        ckey = consumer_key or settings.snaptrade_consumer_key
        if not cid or not ckey:
            raise SnapTradeError(
                "SNAPTRADE_CLIENT_ID and SNAPTRADE_CONSUMER_KEY are required; "
                "set SNAPTRADE_USE_MOCK=true to run without credentials"
            )

        # Personal keys carry the "PERS-" prefix and use a different auth model
        # (no registerUser, no per-user secret) — see ``_PERSONAL_SENTINEL``.
        self._personal = cid.upper().startswith("PERS-")
        # The auth mode MUST be declared. It is what makes the SDK stamp each
        # request with a `timestamp` query param and an HMAC `Signature` header;
        # left unset the SDK falls back to sending *both* the commercial and
        # personal clientId params and neither credential, and every call comes
        # back 401.
        auth = (
            SnapTradeAuth.personal_api_key(client_id=cid, consumer_key=ckey)
            if self._personal
            else SnapTradeAuth.commercial_api_key(client_id=cid, consumer_key=ckey)
        )
        self._client = SnapTrade(auth=auth)

    def _auth(self, user_id: str, user_secret: str) -> tuple[str | None, str | None]:
        """(user_id, user_secret) to send on a request.

        Personal keys resolve identity from the signed consumerKey, so these
        must be omitted (``None``); the stored sentinel is never sent.
        """
        if self._personal:
            return None, None
        return user_id, user_secret

    async def register_user(self, app_user_id: str) -> SnapTradeRegistration:
        if self._personal:
            # The Personal user is provisioned at signup; registerUser is
            # rejected (400/1012). No per-user secret exists — hand the service
            # a sentinel so its "is a brokerage linked?" checks pass. Requests
            # authenticate via the consumerKey, not this value.
            return SnapTradeRegistration(
                snaptrade_user_id=_PERSONAL_SENTINEL, user_secret=_PERSONAL_SENTINEL
            )
        try:
            response = await asyncio.to_thread(
                self._client.authentication.register_snap_trade_user,
                body={"userId": app_user_id},
            )
        except Exception as exc:
            raise SnapTradeError(f"Failed to register SnapTrade user: {exc}") from exc

        body = _body(response)
        secret = _get(body, "userSecret", "user_secret")
        if not secret:
            raise SnapTradeError("SnapTrade registration returned no userSecret")
        return SnapTradeRegistration(
            snaptrade_user_id=str(_get(body, "userId", "user_id", default=app_user_id)),
            user_secret=str(secret),
        )

    async def create_connection_portal_url(
        self, *, snaptrade_user_id: str, user_secret: str, custom_redirect: str | None = None
    ) -> str:
        uid, usecret = self._auth(snaptrade_user_id, user_secret)
        try:
            response = await asyncio.to_thread(
                self._client.authentication.login_snap_trade_user,
                user_id=uid,
                user_secret=usecret,
                # Ask for a trade-capable connection where the broker supports
                # it, falling back to read-only rather than failing the link.
                connection_type="trade-if-available",
                # Where the portal's "Done" button sends the user. Without this
                # the button has nowhere to go and appears to do nothing — the
                # client passes its own deep link so control returns to the app.
                custom_redirect=custom_redirect,
                # Belt-and-braces: even in an external browser with no redirect,
                # give the portal a working close button.
                show_close_button=True,
            )
        except Exception as exc:
            raise SnapTradeError(f"Failed to create connection portal: {exc}") from exc

        url = _get(_body(response), "redirectURI", "redirect_uri")
        if not url:
            raise SnapTradeError("SnapTrade login returned no redirect URI")
        return str(url)

    async def list_accounts(
        self, *, snaptrade_user_id: str, user_secret: str
    ) -> list[SnapTradeAccount]:
        uid, usecret = self._auth(snaptrade_user_id, user_secret)
        try:
            response = await asyncio.to_thread(
                self._client.account_information.list_user_accounts,
                user_id=uid,
                user_secret=usecret,
            )
        except Exception as exc:
            raise SnapTradeError(f"Failed to list accounts: {exc}") from exc

        accounts: list[SnapTradeAccount] = []
        for raw in _body(response) or []:
            balance = _get(raw, "balance", default={})
            accounts.append(
                SnapTradeAccount(
                    account_id=str(_get(raw, "id", default="")),
                    name=str(_get(raw, "name", default="Brokerage account")),
                    number_masked=_get(raw, "number"),
                    institution_name=_get(raw, "institution_name", "brokerage"),
                    currency=str(_get(raw, "currency", default="CAD") or "CAD"),
                    total_value_cents=_to_cents(_get(balance, "total", "amount")),
                    cash_cents=_to_cents(_get(balance, "cash")),
                )
            )
        return accounts

    async def list_positions(
        self, *, snaptrade_user_id: str, user_secret: str, account_id: str
    ) -> list[SnapTradePosition]:
        uid, usecret = self._auth(snaptrade_user_id, user_secret)
        try:
            response = await asyncio.to_thread(
                self._client.account_information.get_all_account_positions,
                user_id=uid,
                user_secret=usecret,
                account_id=account_id,
            )
        except Exception as exc:
            raise SnapTradeError(f"Failed to list positions: {exc}") from exc

        positions: list[SnapTradePosition] = []
        for raw in _body(response) or []:
            symbol_obj = _get(raw, "symbol", default={})
            # Positions nest the instrument one level deeper than quotes do.
            inner = _get(symbol_obj, "symbol", default=symbol_obj)
            ticker = _normalize_ticker(inner)
            if not ticker:
                continue
            qty = _get(raw, "units", "fractional_units", default=0) or 0
            positions.append(
                SnapTradePosition(
                    symbol=ticker,
                    name=_get(inner, "description", "name"),
                    qty=float(qty),
                    avg_cost_cents=_to_cents(_get(raw, "average_purchase_price")),
                    currency=str(
                        _get(_get(inner, "currency", default={}), "code", default="CAD") or "CAD"
                    ),
                    market_value_cents=_to_cents(_get(raw, "market_value")) or None,
                )
            )
        return positions

    async def resolve_symbol(
        self,
        *,
        snaptrade_user_id: str,
        user_secret: str,
        account_id: str,
        ticker: str,
    ) -> SnapTradeSymbol | None:
        # Search on the bare ticker: the broker indexes "VFV", not "VFV.TO".
        bare = ticker.upper().split(".")[0]
        uid, usecret = self._auth(snaptrade_user_id, user_secret)
        try:
            response = await asyncio.to_thread(
                self._client.reference_data.symbol_search_user_account,
                user_id=uid,
                user_secret=usecret,
                account_id=account_id,
                substring=bare,
            )
        except Exception as exc:
            raise SnapTradeError(f"Symbol lookup failed for {ticker}: {exc}") from exc

        for raw in _body(response) or []:
            if _normalize_ticker(raw) == ticker.upper():
                symbol_id = _get(raw, "id", "universal_symbol_id")
                if symbol_id:
                    return SnapTradeSymbol(
                        universal_symbol_id=str(symbol_id),
                        ticker=ticker.upper(),
                        description=_get(raw, "description"),
                    )
        return None

    async def place_order(
        self,
        *,
        snaptrade_user_id: str,
        user_secret: str,
        account_id: str,
        universal_symbol_id: str,
        action: OrderAction,
        order_type: OrderType,
        qty: float,
        limit_price_cents: int | None = None,
        client_order_id: str | None = None,
    ) -> SnapTradeOrderResult:
        uid, usecret = self._auth(snaptrade_user_id, user_secret)
        kwargs: dict[str, Any] = {
            "user_id": uid,
            "user_secret": usecret,
            "account_id": account_id,
            "universal_symbol_id": universal_symbol_id,
            "action": action,
            "order_type": order_type,
            # Day orders only: a GTC order could fill days after the user
            # approved it, against stale analysis.
            "time_in_force": "Day",
            "units": qty,
        }
        if limit_price_cents is not None:
            kwargs["price"] = round(limit_price_cents / 100, 2)
        if client_order_id:
            kwargs["client_order_id"] = client_order_id

        logger.info(
            "Placing live brokerage order",
            extra={"extra": {"action": action, "qty": qty, "type": order_type}},
        )
        try:
            response = await asyncio.to_thread(self._client.trading.place_force_order, **kwargs)
        except Exception as exc:
            raise SnapTradeError(f"Order placement failed: {exc}") from exc

        return _map_order(_body(response))

    async def get_order_status(
        self,
        *,
        snaptrade_user_id: str,
        user_secret: str,
        account_id: str,
        broker_order_id: str,
    ) -> SnapTradeOrderResult:
        uid, usecret = self._auth(snaptrade_user_id, user_secret)
        try:
            response = await asyncio.to_thread(
                self._client.account_information.get_user_account_orders,
                user_id=uid,
                user_secret=usecret,
                account_id=account_id,
                state="all",
            )
        except Exception as exc:
            raise SnapTradeError(f"Failed to fetch order status: {exc}") from exc

        for raw in _body(response) or []:
            if str(_get(raw, "brokerage_order_id", "id", default="")) == broker_order_id:
                return _map_order(raw)

        # Not in the window the broker returns. Report unknown rather than
        # inventing a terminal state — the caller keeps the order open.
        return SnapTradeOrderResult(
            broker_order_id=broker_order_id,
            state="submitted",
            detail="Order not found in recent broker history",
            updated_at=datetime.now(UTC),
        )
