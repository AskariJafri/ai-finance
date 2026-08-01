"""SnapTrade gateway interface + framework-free DTOs.

SnapTrade is a brokerage aggregator: it fronts brokers that expose no public
API of their own (Wealthsimple, Questrade) with a single normalized interface,
including *order placement*, not just read access.

Services depend only on this abstraction, never on ``snaptrade_client`` types.
Same rationale as the Plaid gateway: the vendor SDK stays at the edge, the
whole link/sync/execute pipeline is unit-testable against ``MockSnapTradeClient``,
and swapping SDK versions touches exactly one adapter.

Money crossing this boundary is integer **cents** in the instrument's native
currency (a VFV.TO position reports CAD cents), matching ``Holding``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

OrderAction = Literal["BUY", "SELL"]
OrderType = Literal["Market", "Limit"]

# SnapTrade's order lifecycle, normalized. Deliberately mirrors
# ``ProposedOrderStatus`` so the service layer maps 1:1 without a lookup table.
BrokerOrderState = Literal["submitted", "filled", "rejected", "cancelled", "failed"]


@dataclass(frozen=True)
class SnapTradeRegistration:
    """Result of registering our user with SnapTrade.

    ``user_secret`` is a bearer credential for this user's brokerage data — it
    is encrypted at rest exactly like a Plaid access token.
    """

    snaptrade_user_id: str
    user_secret: str


@dataclass(frozen=True)
class SnapTradeAccount:
    account_id: str
    name: str
    number_masked: str | None
    institution_name: str | None
    currency: str
    total_value_cents: int
    cash_cents: int


@dataclass(frozen=True)
class SnapTradePosition:
    """A position as the broker reports it.

    ``symbol`` is normalized to the Yahoo-style ticker the rest of the app uses
    (``VFV.TO``), so it joins directly against ``Holding.symbol`` and the market
    data provider.
    """

    symbol: str
    name: str | None
    qty: float
    avg_cost_cents: int  # per share, native currency
    currency: str
    market_value_cents: int | None = None


@dataclass(frozen=True)
class SnapTradeSymbol:
    """Broker-side instrument id, required to place an order.

    Tickers are not portable across brokers, so an order must reference the
    ``universal_symbol_id`` resolved for the specific account.
    """

    universal_symbol_id: str
    ticker: str
    description: str | None = None


@dataclass(frozen=True)
class SnapTradeOrderResult:
    """Outcome of placing or polling a single order."""

    broker_order_id: str | None
    state: BrokerOrderState
    filled_qty: float | None = None
    fill_price_cents: int | None = None  # per share, native currency
    detail: str | None = None
    updated_at: datetime | None = None


class SnapTradeError(RuntimeError):
    """Any failure crossing the SnapTrade boundary.

    Services catch this rather than vendor exceptions, so an SDK upgrade cannot
    leak new exception types into the order state machine.
    """


@runtime_checkable
class SnapTradeGateway(Protocol):
    """The operations the app needs from a brokerage aggregator."""

    async def register_user(self, app_user_id: str) -> SnapTradeRegistration:
        """Create the SnapTrade-side user. Idempotent per ``app_user_id``."""
        ...

    async def create_connection_portal_url(
        self,
        *,
        snaptrade_user_id: str,
        user_secret: str,
        custom_redirect: str | None = None,
    ) -> str:
        """One-time URL where the user authorizes their broker.

        Credentials are entered on SnapTrade's portal — they never reach this
        app, which is the whole reason for using an aggregator over an
        unofficial wrapper. ``custom_redirect`` is where the portal returns the
        user when they finish (the client's deep link), so the "Done" button
        actually goes somewhere.
        """
        ...

    async def list_accounts(
        self, *, snaptrade_user_id: str, user_secret: str
    ) -> list[SnapTradeAccount]: ...

    async def list_positions(
        self, *, snaptrade_user_id: str, user_secret: str, account_id: str
    ) -> list[SnapTradePosition]: ...

    async def resolve_symbol(
        self,
        *,
        snaptrade_user_id: str,
        user_secret: str,
        account_id: str,
        ticker: str,
    ) -> SnapTradeSymbol | None:
        """Map an app ticker to this account's broker-side instrument id."""
        ...

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
        """Place a real order.

        ``client_order_id`` carries our ``ProposedOrder.id`` so a retry after a
        network failure is traceable to the same intent.
        """
        ...

    async def get_order_status(
        self,
        *,
        snaptrade_user_id: str,
        user_secret: str,
        account_id: str,
        broker_order_id: str,
    ) -> SnapTradeOrderResult: ...
