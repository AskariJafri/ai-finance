"""In-memory mock SnapTrade adapter for offline dev and tests.

Simulates a linked Wealthsimple account holding Canadian-listed ETFs, and a
broker that fills market orders immediately at a deterministic price. No
network call, no real money — this is what runs unless SnapTrade credentials
are configured *and* ``SNAPTRADE_TRADING_ENABLED`` is set.

Orders are held in per-instance state so a place → poll round trip behaves
like the real thing: ``place_order`` returns ``submitted``, and the first
``get_order_status`` reports the fill. That two-step is deliberate — it keeps
the service layer's polling path exercised in tests instead of short-circuiting
on an instantly-filled order.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.integrations.snaptrade.gateway import (
    BrokerOrderState,
    OrderAction,
    OrderType,
    SnapTradeAccount,
    SnapTradeGateway,
    SnapTradeOrderResult,
    SnapTradePosition,
    SnapTradeRegistration,
    SnapTradeSymbol,
)

_ACCOUNT_ID = "mock-ws-tfsa"

# Deterministic prices in native-currency cents, used for both position
# valuation and simulated fills.
_PRICES_CENTS: dict[str, int] = {
    "VFV.TO": 14_250,
    "XEQT.TO": 3_180,
    "AAPL": 21_400,
}


def _mock_positions() -> list[SnapTradePosition]:
    return [
        SnapTradePosition(
            symbol="VFV.TO",
            name="Vanguard S&P 500 Index ETF",
            qty=42.0,
            avg_cost_cents=11_900,
            currency="CAD",
            market_value_cents=int(42.0 * _PRICES_CENTS["VFV.TO"]),
        ),
        SnapTradePosition(
            symbol="XEQT.TO",
            name="iShares Core Equity ETF Portfolio",
            qty=115.0,
            avg_cost_cents=2_940,
            currency="CAD",
            market_value_cents=int(115.0 * _PRICES_CENTS["XEQT.TO"]),
        ),
    ]


class MockSnapTradeClient(SnapTradeGateway):
    def __init__(self) -> None:
        # broker_order_id -> (state, qty, fill_price_cents)
        self._orders: dict[str, tuple[BrokerOrderState, float, int]] = {}

    async def register_user(self, app_user_id: str) -> SnapTradeRegistration:
        return SnapTradeRegistration(
            snaptrade_user_id=app_user_id,
            user_secret=f"mock-secret-{app_user_id}",
        )

    async def create_connection_portal_url(
        self, *, snaptrade_user_id: str, user_secret: str, custom_redirect: str | None = None
    ) -> str:
        base = "https://app.snaptrade.com/mock-connection-portal"
        return f"{base}?customRedirect={custom_redirect}" if custom_redirect else base

    async def list_accounts(
        self, *, snaptrade_user_id: str, user_secret: str
    ) -> list[SnapTradeAccount]:
        positions_value = sum(p.market_value_cents or 0 for p in _mock_positions())
        cash_cents = 125_000
        return [
            SnapTradeAccount(
                account_id=_ACCOUNT_ID,
                name="TFSA",
                number_masked="****4321",
                institution_name="Wealthsimple",
                currency="CAD",
                total_value_cents=positions_value + cash_cents,
                cash_cents=cash_cents,
            )
        ]

    async def list_positions(
        self, *, snaptrade_user_id: str, user_secret: str, account_id: str
    ) -> list[SnapTradePosition]:
        return _mock_positions()

    async def resolve_symbol(
        self,
        *,
        snaptrade_user_id: str,
        user_secret: str,
        account_id: str,
        ticker: str,
    ) -> SnapTradeSymbol | None:
        key = ticker.upper()
        if key not in _PRICES_CENTS:
            return None
        return SnapTradeSymbol(
            universal_symbol_id=f"mock-sym-{key}",
            ticker=key,
            description=f"Mock instrument {key}",
        )

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
        ticker = universal_symbol_id.removeprefix("mock-sym-")
        price = limit_price_cents or _PRICES_CENTS.get(ticker, 10_000)
        broker_order_id = f"mock-order-{uuid.uuid4().hex[:12]}"
        self._orders[broker_order_id] = ("filled", qty, price)
        return SnapTradeOrderResult(
            broker_order_id=broker_order_id,
            state="submitted",
            detail=f"Mock {action} {qty} @ {order_type}",
            updated_at=datetime.now(UTC),
        )

    async def get_order_status(
        self,
        *,
        snaptrade_user_id: str,
        user_secret: str,
        account_id: str,
        broker_order_id: str,
    ) -> SnapTradeOrderResult:
        entry = self._orders.get(broker_order_id)
        if entry is None:
            return SnapTradeOrderResult(
                broker_order_id=broker_order_id,
                state="failed",
                detail="Unknown order id",
                updated_at=datetime.now(UTC),
            )
        state, qty, price = entry
        return SnapTradeOrderResult(
            broker_order_id=broker_order_id,
            state=state,
            filled_qty=qty,
            fill_price_cents=price,
            detail="Mock fill",
            updated_at=datetime.now(UTC),
        )
