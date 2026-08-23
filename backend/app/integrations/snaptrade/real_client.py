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
# ``Holding.symbol`` and the market data provider. Both spellings are here on
# purpose: older payloads carry a friendly code ("TSX"), current ones carry the
# ISO MIC ("XTSE"). US venues need no suffix, which is what the empty default
# gives them.
_EXCHANGE_SUFFIX = {
    "TSX": ".TO",
    "XTSE": ".TO",
    "XTSX": ".TO",
    "TSXV": ".V",
    "XTSV": ".V",
    "CSE": ".CN",
    "XCNQ": ".CN",
    "NEO": ".NE",
    "NEOE": ".NE",
}

# Personal SnapTrade keys (clientId prefix "PERS-") authenticate via the signed
# consumerKey alone: there is no ``registerUser`` and no per-user secret — you
# omit userId/userSecret on every request (the key *is* the identity). The
# service layer still expects a (user_id, secret) pair to store and thread
# through, so registration returns this sentinel; it is never sent to SnapTrade
# (see ``RealSnapTradeClient._auth``).
_PERSONAL_SENTINEL = "personal-key-user"


def _amount(value: Any) -> Any:
    """Unwrap SnapTrade's ``{"amount": 478.23, "currency": "CAD"}`` money object.

    Balances nest the figure one level down; quantities and prices are bare
    numbers (sometimes strings). Passing everything through here means a
    payload that changes shape costs nothing.
    """
    if isinstance(value, dict):
        return value.get("amount")
    return value


def _to_cents(amount: Any) -> int:
    """Money -> integer cents. ``None``/unparseable becomes 0."""
    amount = _amount(amount)
    if amount is None:
        return 0
    try:
        return int(round(float(amount) * 100))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any, default: float = 0.0) -> float:
    """Quantity/price -> float. SnapTrade sends these as decimal *strings*."""
    value = _amount(value)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _body(response: Any) -> Any:
    """SDK responses expose ``.body``; tolerate plain dicts/lists too."""
    return getattr(response, "body", response)


def _results(response: Any) -> list[Any]:
    """The rows of a list response, whichever envelope it arrived in.

    Current SnapTrade wraps collections as ``{"results": [...],
    "data_freshness": {...}}``; older ones returned a bare list. Iterating the
    dict form directly yields its *keys*, which silently maps to zero rows —
    that is precisely how a whole portfolio went missing, so the unwrapping is
    centralized here rather than repeated per endpoint.
    """
    body = _body(response)
    if isinstance(body, dict):
        body = body.get("results", [])
    return list(body) if isinstance(body, list) else []


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
    # ``exchange`` is a bare MIC string on current payloads and an object on
    # older ones.
    code = exchange if isinstance(exchange, str) else _get(exchange, "code", "mic_code", default="")
    return f"{ticker}{_EXCHANGE_SUFFIX.get(str(code or '').upper(), '')}"


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
        for raw in _results(response):
            balance = _get(raw, "balance", default={})
            meta = _get(raw, "meta", default={})
            account_id = str(_get(raw, "id", default=""))
            # ``meta`` is the reliable source: the top-level ``currency`` comes
            # back null for Wealthsimple, and ``status`` only appears there on
            # some payloads.
            currency = str(_get(meta, "currency") or _get(raw, "currency") or "CAD")
            status = str(_get(raw, "status") or _get(meta, "status") or "open")
            is_open = status.lower() not in {"closed", "archived", "deleted"}
            cash_cents = 0
            if is_open:
                cash_cents = await self._cash_cents(
                    snaptrade_user_id=snaptrade_user_id,
                    user_secret=user_secret,
                    account_id=account_id,
                    currency=currency,
                )
            accounts.append(
                SnapTradeAccount(
                    account_id=account_id,
                    name=str(_get(raw, "name", default="Brokerage account")),
                    number_masked=_get(raw, "number"),
                    institution_name=_get(raw, "institution_name", "brokerage"),
                    currency=currency,
                    # ``balance.total`` is a money object, not a number.
                    total_value_cents=_to_cents(_get(balance, "total", "amount")),
                    cash_cents=cash_cents,
                    is_open=is_open,
                )
            )
        return accounts

    async def _cash_cents(
        self, *, snaptrade_user_id: str, user_secret: str, account_id: str, currency: str
    ) -> int:
        """Settled cash for one account.

        The accounts payload carries only ``balance.total`` (positions plus
        cash), so cash needs its own call. A failure here costs the cash figure,
        not the whole account list — the positions are the point.
        """
        uid, usecret = self._auth(snaptrade_user_id, user_secret)
        try:
            response = await asyncio.to_thread(
                self._client.account_information.get_user_account_balance,
                user_id=uid,
                user_secret=usecret,
                account_id=account_id,
            )
        except Exception as exc:
            logger.warning("Cash balance unavailable for account %s: %s", account_id, exc)
            return 0

        total = 0
        for row in _results(response):
            code = str(_get(_get(row, "currency", default={}), "code", default="") or "").upper()
            if code and code != currency.upper():
                continue
            total += _to_cents(_get(row, "cash"))
        return total

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
        for raw in _results(response):
            # Current payloads put the instrument under ``instrument``; older
            # ones nested it as ``symbol.symbol``.
            symbol_obj = _get(raw, "instrument", "symbol", default={})
            # Legacy payloads nest the instrument a further level down
            # (``symbol.symbol`` is an object); current ones put the ticker
            # string there instead, so only descend into a nested object.
            nested = _get(symbol_obj, "symbol")
            inner = nested if isinstance(nested, dict) else symbol_obj
            ticker = _normalize_ticker(inner)
            if not ticker:
                logger.warning("Skipping position with unreadable symbol: %r", raw)
                continue
            qty = _to_float(_get(raw, "units", "fractional_units"))
            price = _to_float(_get(raw, "price"))
            # ``currency`` is a bare code on the position and an object on the
            # instrument, depending on payload generation.
            currency_obj = _get(raw, "currency") or _get(inner, "currency", default={})
            currency = (
                currency_obj
                if isinstance(currency_obj, str)
                else _get(currency_obj, "code", default="CAD")
            )
            market_value_cents = _to_cents(_get(raw, "market_value"))
            if not market_value_cents and price:
                market_value_cents = _to_cents(qty * price)
            positions.append(
                SnapTradePosition(
                    symbol=ticker,
                    name=_get(inner, "description", "name"),
                    qty=qty,
                    # Per-share cost. ``cost_basis`` is the current spelling.
                    avg_cost_cents=_to_cents(_get(raw, "cost_basis", "average_purchase_price")),
                    currency=str(currency or "CAD").upper(),
                    market_value_cents=market_value_cents or None,
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

        for raw in _results(response):
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

        for raw in _results(response):
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
