"""Linking a real brokerage account, mirroring its positions, and executing
approved orders against it.

Safety model — three independent gates, all of which must pass before a real
order reaches a real broker:

1. ``settings.live_trading_enabled`` — env-level kill switch, off by default.
2. ``BrokerageConnection.trading_enabled`` — the broker granted trade access.
3. ``ProposedOrder.status == APPROVED`` — a human approved *this* order.

Nothing in the AI committee, auto-trader or rebalancer may set APPROVED; they
only ever produce PROPOSED rows. Approval is a user action routed through
``approve_and_submit``, so the recommend-only guarantee the rest of the invest
module documents still holds — execution is a convenience on top of it, not a
replacement for it.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import TokenCipher
from app.core.logging import get_logger
from app.db.models import BrokerageConnection, ProposedOrder
from app.domain.book_value import next_book_value_cad_cents
from app.domain.enums import BrokerageConnectionStatus, OrderSide, ProposedOrderStatus
from app.integrations.snaptrade.gateway import (
    OrderAction,
    OrderType,
    SnapTradeError,
    SnapTradeGateway,
    SnapTradePosition,
)
from app.repositories.ai_insights import AIInsightRepository
from app.repositories.brokerage_connections import BrokerageConnectionRepository
from app.repositories.holdings import HoldingRepository
from app.repositories.proposed_orders import ProposedOrderRepository
from app.repositories.user_settings import UserSettingsRepository
from app.schemas.brokerage import (
    BrokerageConnectionOut,
    BrokerageLinkOut,
    BrokerageSyncOut,
)
from app.schemas.invest import ProposedOrderOut
from app.services.errors import NotFound, ValidationFailed
from app.services.invest_insight_service import KIND_HEALTH
from app.services.invest_service import InvestService, order_out

logger = get_logger(__name__)


def _combine(existing: SnapTradePosition | None, incoming: SnapTradePosition) -> SnapTradePosition:
    """Fold the same ticker held in two accounts into one position.

    Quantities add; the cost basis is the share-weighted average of the two, so
    the unrealized P/L the app shows matches what the user would compute across
    both accounts by hand.
    """
    if existing is None:
        return incoming
    qty = existing.qty + incoming.qty
    if qty <= 0:
        return incoming
    avg_cost_cents = round(
        (existing.avg_cost_cents * existing.qty + incoming.avg_cost_cents * incoming.qty) / qty
    )
    # Market values add — but only when both sides reported one. A partial sum
    # would understate the position by whichever account stayed silent, which is
    # worse than falling back to market data for the whole thing.
    market_value_cents = (
        existing.market_value_cents + incoming.market_value_cents
        if existing.market_value_cents is not None and incoming.market_value_cents is not None
        else None
    )
    return replace(
        existing,
        qty=qty,
        avg_cost_cents=avg_cost_cents,
        market_value_cents=market_value_cents,
    )


class BrokerageService:
    def __init__(
        self,
        session: AsyncSession,
        cipher: TokenCipher,
        gateway: SnapTradeGateway,
        invest: InvestService,
    ) -> None:
        self.session = session
        self.cipher = cipher
        self.gateway = gateway
        self.invest = invest
        self.connections = BrokerageConnectionRepository(session)
        self.holdings = HoldingRepository(session)
        self.proposals = ProposedOrderRepository(session)
        self.user_settings = UserSettingsRepository(session)
        self.insights = AIInsightRepository(session)

    # --- credentials ---
    async def _credentials(self, user_id: uuid.UUID) -> tuple[str, str]:
        """(snaptrade_user_id, user_secret) for this user, decrypted."""
        row = await self.user_settings.get_for_user(user_id)
        if row is None or not row.snaptrade_user_id or not row.snaptrade_secret_encrypted:
            raise ValidationFailed("No brokerage account linked. Start a link from Settings first.")
        return row.snaptrade_user_id, self.cipher.decrypt(row.snaptrade_secret_encrypted)

    # --- linking ---
    async def start_link(
        self, user_id: uuid.UUID, redirect_uri: str | None = None
    ) -> BrokerageLinkOut:
        """Register with SnapTrade if needed and return the portal URL.

        The user enters broker credentials on SnapTrade's portal — they never
        touch this app, which is the reason for using an aggregator rather than
        an unofficial wrapper that would need the password. ``redirect_uri`` is
        the client's deep link the portal returns to when the user finishes.
        """
        row = await self.user_settings.get_for_user(user_id)
        if row is not None and row.snaptrade_user_id and row.snaptrade_secret_encrypted:
            snaptrade_user_id = row.snaptrade_user_id
            secret = self.cipher.decrypt(row.snaptrade_secret_encrypted)
        else:
            try:
                registration = await self.gateway.register_user(str(user_id))
            except SnapTradeError as exc:
                raise ValidationFailed(str(exc)) from exc
            snaptrade_user_id = registration.snaptrade_user_id
            secret = registration.user_secret
            await self.user_settings.set_snaptrade_registration(
                user_id,
                snaptrade_user_id=snaptrade_user_id,
                secret_encrypted=self.cipher.encrypt(secret),
            )

        try:
            url = await self.gateway.create_connection_portal_url(
                snaptrade_user_id=snaptrade_user_id,
                user_secret=secret,
                custom_redirect=redirect_uri,
            )
        except SnapTradeError as exc:
            raise ValidationFailed(str(exc)) from exc

        await self.session.commit()
        return BrokerageLinkOut(portal_url=url)

    async def complete_link(self, user_id: uuid.UUID) -> list[BrokerageConnectionOut]:
        """Pull the accounts the user just authorized and store them.

        Called after the portal redirects back. Idempotent — re-running it
        refreshes balances rather than duplicating connections.
        """
        snaptrade_user_id, secret = await self._credentials(user_id)
        try:
            accounts = await self.gateway.list_accounts(
                snaptrade_user_id=snaptrade_user_id, user_secret=secret
            )
        except SnapTradeError as exc:
            raise ValidationFailed(str(exc)) from exc

        if not accounts:
            raise ValidationFailed(
                "No brokerage accounts found. Finish authorizing in the portal, then try again."
            )

        # A closed account keeps coming back from SnapTrade forever. Storing it
        # would leave the user staring at an account they deliberately disabled,
        # with no way to remove it short of unlinking everything.
        live = [a for a in accounts if a.is_open]
        if not live:
            raise ValidationFailed(
                "Every account on this connection is closed at the broker. "
                "Authorize an open account, then try again."
            )

        rows = []
        for account in live:
            rows.append(
                await self.connections.upsert(
                    user_id=user_id,
                    brokerage_account_id=account.account_id,
                    account_name=account.name,
                    institution_name=account.institution_name,
                    number_masked=account.number_masked,
                    currency=account.currency,
                    cash_cents=account.cash_cents,
                    total_value_cents=account.total_value_cents,
                    # SnapTrade does not report trade capability per account on
                    # the accounts payload; gate on the env switch and let a
                    # rejected order surface the truth from the broker.
                    trading_enabled=settings.live_trading_enabled,
                )
            )

        # Drop connections the broker no longer reports as live — an account
        # closed or de-authorized since the last link. Their mirrored holdings
        # go with them on the sync below, which prunes anything unseen.
        keep = {a.account_id for a in live}
        for existing in await self.connections.list_for_user(user_id):
            if existing.brokerage_account_id not in keep:
                await self.connections.delete(existing)
        await self.session.commit()

        # Pull the positions in the same breath. Leaving this to a separate
        # button is why a linked account could sit there showing nothing.
        try:
            await self.sync_positions(user_id)
        except ValidationFailed as exc:
            logger.warning("Link succeeded but position sync failed: %s", exc)

        return [_connection_out(r) for r in await self.connections.list_for_user(user_id)]

    async def list_connections(self, user_id: uuid.UUID) -> list[BrokerageConnectionOut]:
        return [_connection_out(r) for r in await self.connections.list_for_user(user_id)]

    async def unlink(self, user_id: uuid.UUID) -> None:
        """Drop the link and the positions mirrored from it.

        Manually-entered holdings survive; only ``is_synced`` rows go, since
        without the connection they can no longer be refreshed.
        """
        for holding in await self.holdings.list_synced(user_id):
            await self.holdings.delete(user_id, holding.id)
        await self.connections.delete_all_for_user(user_id)
        await self.user_settings.clear_snaptrade_registration(user_id)
        await self.session.commit()

    async def resync_after_fill(self, user_id: uuid.UUID) -> bool:
        """Re-mirror positions once the user has marked a trade done.

        For a linked account the app is a mirror, not a ledger: whatever the
        broker reports is what the user holds. ``apply_fill`` moves the local
        numbers so the screen reacts immediately, but its arithmetic is an
        estimate -- it guesses the fill price, and it can only guess the cost
        basis. Pulling the broker's own positions right afterwards replaces
        that estimate with the truth instead of letting the two drift apart
        until the user happens to press Sync.

        If the broker has not booked the trade yet, its answer is still the
        honest one: the account really does not show the trade yet, and the
        next sync will pick it up.

        Returns True when the broker's numbers were applied. Never raises --
        the fill itself is already committed, and a sync that could not run is
        not a reason to tell the user their trade failed.
        """
        if not await self.connections.list_for_user(user_id):
            return False
        try:
            await self.sync_positions(user_id)
            return True
        except (ValidationFailed, SnapTradeError) as exc:
            logger.warning("Post-fill position sync failed for user %s: %s", user_id, exc)
            return False

    # --- position sync ---
    async def sync_positions(self, user_id: uuid.UUID) -> BrokerageSyncOut:
        """Mirror broker positions into ``Holding``. The broker is authoritative.

        Synced rows absent from the broker's response are deleted (position was
        closed elsewhere); manual rows are never touched.
        """
        snaptrade_user_id, secret = await self._credentials(user_id)
        connections = await self.connections.list_for_user(user_id)
        if not connections:
            raise ValidationFailed("No brokerage accounts linked.")

        # One call covering every account, rather than one per connection.
        try:
            accounts = await self.gateway.list_accounts(
                snaptrade_user_id=snaptrade_user_id, user_secret=secret
            )
        except SnapTradeError as exc:
            raise ValidationFailed(f"Sync failed: {exc}") from exc
        by_account = {a.account_id: a for a in accounts}

        # Refresh the rate before converting anything with it. `sync_bars`
        # always covers the FX symbol, so an empty symbol list is enough — and
        # on a first-ever sync it is what stops a USD position being booked at
        # par, which a book value would then carry forever.
        await self.invest.market_data.sync_bars([], refresh_today=True)
        await self.session.commit()
        fx = await self.invest.market_data.latest_fx_rate() or 1.0
        # Positions accumulate across accounts before anything is written:
        # ``Holding`` is unique on (user, symbol), so writing per account would
        # let a second account holding the same ticker overwrite the first
        # rather than add to it.
        merged: dict[str, SnapTradePosition] = {}
        failures: list[str] = []
        cash_cents = 0

        for connection in connections:
            account = by_account.get(connection.brokerage_account_id)
            if account is not None and not account.is_open:
                # Closed at the broker since the last sync. Drop it rather than
                # keep reporting a dead account as ACTIVE forever.
                await self.connections.delete(connection)
                continue

            try:
                positions = await self.gateway.list_positions(
                    snaptrade_user_id=snaptrade_user_id,
                    user_secret=secret,
                    account_id=connection.brokerage_account_id,
                )
            except SnapTradeError as exc:
                # One unreachable account must not cost the user every other
                # account's positions, so record it and carry on.
                logger.warning(
                    "Position sync failed for account %s: %s",
                    connection.brokerage_account_id,
                    exc,
                )
                await self.connections.mark_status(connection, BrokerageConnectionStatus.ERROR)
                failures.append(connection.account_name or connection.brokerage_account_id)
                continue

            if account is not None:
                connection.cash_cents = account.cash_cents
                connection.total_value_cents = account.total_value_cents
            # Sub-accounts can be denominated differently; the app's cash figure
            # is CAD, so convert before summing.
            rate = fx if connection.currency.upper() == "USD" else 1.0
            cash_cents += round(connection.cash_cents * rate)

            for position in positions:
                if position.qty <= 0:
                    continue
                key = position.symbol.upper()
                merged[key] = _combine(merged.get(key), position)

            connection.status = BrokerageConnectionStatus.ACTIVE
            connection.last_synced_at = datetime.now(UTC)

        valued_at = datetime.now(UTC)
        # Read the rows as they were before this sync overwrites them: the CAD
        # book rolls forward from the previous cost, and the previous cost is
        # about to be replaced.
        before = {h.symbol.upper(): h for h in await self.holdings.list_for_user(user_id)}
        # Whether this account has ever been mirrored. On the first sync every
        # position already existed, so no rate we apply today is the rate its
        # shares were bought at. Afterwards, a symbol appearing for the first
        # time is a position opened since the last sync — bought at a rate we
        # can read, so its book is exact.
        first_ever = not any(h.is_synced for h in before.values())
        for symbol, position in merged.items():
            existing = before.get(symbol)
            rate = fx if position.currency.upper() == "USD" else 1.0
            book_cad = next_book_value_cad_cents(
                previous_cad_cents=existing.book_value_cad_cents if existing else None,
                previous_native_cost_cents=(
                    round(existing.avg_cost_cents * existing.qty) if existing else 0
                ),
                native_cost_cents=round(position.avg_cost_cents * position.qty),
                fx_rate=rate,
            )
            await self.holdings.upsert(
                user_id=user_id,
                symbol=symbol,
                qty=position.qty,
                avg_cost_cents=position.avg_cost_cents,
                currency=position.currency,
                name=position.name,
                is_synced=True,
                # The broker's own valuation, in the position's currency. This
                # is the figure on the user's brokerage screen; keeping it is
                # what lets the portfolio agree with it instead of re-deriving
                # a near-miss from cached closes and our own FX rate.
                broker_value_cents=position.market_value_cents,
                broker_valued_at=valued_at if position.market_value_cents is not None else None,
                book_value_cad_cents=book_cad,
                book_value_estimated=(
                    first_ever or (existing.book_value_estimated if existing is not None else False)
                ),
            )

        removed = 0
        for holding in await self.holdings.list_synced(user_id):
            if holding.symbol.upper() not in merged:
                await self.holdings.delete(user_id, holding.id)
                removed += 1

        # Keep the rebalancer's cash figure in step with the real account.
        user_settings = await self.user_settings.get_or_create(user_id)
        user_settings.invest_cash_cents = cash_cents

        await self.session.commit()

        # A position with no cached bars prices as "stale" everywhere until an
        # unrelated market-data sync runs, which reads to the user as the
        # holding never having arrived.
        if merged:
            # `refresh_today` because this runs when the user pressed Sync. The
            # cache is right to trust a settled close; it is wrong to trust
            # today's, which moves all session. Without this the first sync of
            # the day pinned every price until tomorrow, so pressing Sync again
            # returned the same numbers however far the market had moved.
            await self.invest.market_data.sync_bars(sorted(merged), refresh_today=True)
            await self.session.commit()

        # The cached portfolio review describes the holdings as they were before
        # this sync; drop it so the next read regenerates against the new ones.
        await self.insights.delete(user_id, KIND_HEALTH, date.today().strftime("%Y-%m"))
        await self.session.commit()

        return BrokerageSyncOut(
            positions_synced=len(merged),
            positions_removed=removed,
            cash_cents=cash_cents,
            failed_accounts=failures,
        )

    # --- execution ---
    async def approve_and_submit(self, user_id: uuid.UUID, order_id: uuid.UUID) -> ProposedOrderOut:
        """Human approval → live order. The only path that can move real money.

        On any failure the order lands in FAILED with the reason in
        ``status_detail`` rather than being retried automatically: a retry loop
        against a broker risks duplicate fills.
        """
        order = await self.proposals.get_for_user(user_id, order_id, for_update=True)
        if order is None:
            raise NotFound("Proposed order not found.")
        if order.status != ProposedOrderStatus.PROPOSED:
            raise ValidationFailed(f"Order is already {order.status.value}.")

        if not settings.live_trading_enabled:
            raise ValidationFailed(
                "Live trading is disabled. Mark the order executed by hand, or "
                "set SNAPTRADE_TRADING_ENABLED=true with valid credentials."
            )

        connection = await self._trading_connection(user_id)
        snaptrade_user_id, secret = await self._credentials(user_id)

        if order.qty is None or order.qty <= 0:
            # Notional/fractional orders are a market-order-only Alpaca feature
            # that the aggregator does not carry across brokers.
            raise ValidationFailed(
                "This order is sized in dollars, and a notional amount can't be "
                "routed through SnapTrade. Re-run the plan with share sizing to "
                "place it here, or buy the dollar amount at your broker and mark "
                "it executed by hand."
            )

        # Never route a sell larger than the position. `apply_fill` already
        # refuses to book one, but that runs on settlement — after the order
        # reached the broker. The check that matters is the one before the
        # request goes out, because an order placed cannot be unplaced.
        #
        # Deliberately a refusal rather than a silent downsize: the quantity
        # came from a plan the user read and approved, and quietly selling a
        # different amount than the one they said yes to is its own failure.
        if order.side == OrderSide.SELL:
            holding = await self.holdings.get(user_id, order.symbol)
            owned = holding.qty if holding else 0.0
            if owned < order.qty - 1e-9:
                return await self._fail(
                    order,
                    f"Your records show {owned:g} shares of {order.symbol}, fewer than the "
                    f"{order.qty:g} this order sells. Tap Sync to refresh positions from "
                    "your broker, then re-run the plan.",
                )

        try:
            symbol = await self.gateway.resolve_symbol(
                snaptrade_user_id=snaptrade_user_id,
                user_secret=secret,
                account_id=connection.brokerage_account_id,
                ticker=order.symbol,
            )
        except SnapTradeError as exc:
            return await self._fail(order, f"Symbol lookup failed: {exc}")

        if symbol is None:
            return await self._fail(order, f"{order.symbol} is not tradable in this account.")

        action: OrderAction = "BUY" if order.side == OrderSide.BUY else "SELL"
        order_type: OrderType = "Limit" if order.order_type == "limit" else "Market"

        order.status = ProposedOrderStatus.APPROVED
        order.brokerage_account_id = connection.brokerage_account_id
        await self.session.flush()

        try:
            result = await self.gateway.place_order(
                snaptrade_user_id=snaptrade_user_id,
                user_secret=secret,
                account_id=connection.brokerage_account_id,
                universal_symbol_id=symbol.universal_symbol_id,
                action=action,
                order_type=order_type,
                qty=order.qty,
                limit_price_cents=order.limit_price_cents,
                # Our own id, so a retry after a network failure is traceable
                # to the same intent rather than looking like a second order.
                client_order_id=str(order.id),
            )
        except SnapTradeError as exc:
            return await self._fail(order, f"Broker rejected the order: {exc}")

        order.broker_order_id = result.broker_order_id
        order.status_detail = result.detail
        order.status = _STATE_MAP.get(result.state, ProposedOrderStatus.SUBMITTED)

        if order.status == ProposedOrderStatus.FILLED:
            await self._apply_fill(user_id, order, result.filled_qty, result.fill_price_cents)

        logger.info(
            "Live order submitted",
            extra={
                "extra": {
                    "symbol": order.symbol,
                    "side": action,
                    "qty": order.qty,
                    "broker_order_id": result.broker_order_id,
                }
            },
        )
        await self.session.commit()
        return order_out(order)

    async def refresh_order(self, user_id: uuid.UUID, order_id: uuid.UUID) -> ProposedOrderOut:
        """Poll the broker for a submitted order and settle it if it filled."""
        order = await self.proposals.get_for_user(user_id, order_id, for_update=True)
        if order is None:
            raise NotFound("Proposed order not found.")
        if order.status != ProposedOrderStatus.SUBMITTED or not order.broker_order_id:
            return order_out(order)

        snaptrade_user_id, secret = await self._credentials(user_id)
        account_id = (
            order.brokerage_account_id
            or (await self._trading_connection(user_id)).brokerage_account_id
        )

        try:
            result = await self.gateway.get_order_status(
                snaptrade_user_id=snaptrade_user_id,
                user_secret=secret,
                account_id=account_id,
                broker_order_id=order.broker_order_id,
            )
        except SnapTradeError as exc:
            raise ValidationFailed(f"Couldn't reach the broker: {exc}") from exc

        new_status = _STATE_MAP.get(result.state, ProposedOrderStatus.SUBMITTED)
        if new_status == ProposedOrderStatus.FILLED:
            await self._apply_fill(user_id, order, result.filled_qty, result.fill_price_cents)
        order.status = new_status
        order.status_detail = result.detail
        await self.session.commit()
        return order_out(order)

    # --- helpers ---
    async def _trading_connection(self, user_id: uuid.UUID) -> BrokerageConnection:
        connections = await self.connections.list_for_user(user_id)
        if not connections:
            raise ValidationFailed("No brokerage account linked.")
        for connection in connections:
            if connection.trading_enabled and (
                connection.status == BrokerageConnectionStatus.ACTIVE
            ):
                return connection
        raise ValidationFailed(
            "Your linked account is read-only. Re-link and grant trade access "
            "to place orders from here."
        )

    async def _apply_fill(
        self,
        user_id: uuid.UUID,
        order: ProposedOrder,
        filled_qty: float | None,
        fill_price_cents: int | None,
    ) -> None:
        await self.invest.apply_fill(
            user_id,
            order,
            qty=filled_qty,
            fill_price=(fill_price_cents / 100) if fill_price_cents else None,
        )

    async def _fail(self, order: ProposedOrder, detail: str) -> ProposedOrderOut:
        order.status = ProposedOrderStatus.FAILED
        order.status_detail = detail
        logger.warning("Live order failed", extra={"extra": {"detail": detail}})
        await self.session.commit()
        return order_out(order)


# Broker lifecycle -> our order status. "submitted" stays open for polling.
_STATE_MAP = {
    "filled": ProposedOrderStatus.FILLED,
    "rejected": ProposedOrderStatus.REJECTED,
    "cancelled": ProposedOrderStatus.EXPIRED,
    "failed": ProposedOrderStatus.FAILED,
    "submitted": ProposedOrderStatus.SUBMITTED,
}


def _connection_out(row: BrokerageConnection) -> BrokerageConnectionOut:
    return BrokerageConnectionOut(
        id=str(row.id),
        brokerage_account_id=row.brokerage_account_id,
        account_name=row.account_name,
        institution_name=row.institution_name,
        number_masked=row.number_masked,
        currency=row.currency,
        cash=row.cash_cents / 100,
        total_value=row.total_value_cents / 100,
        trading_enabled=row.trading_enabled,
        status=row.status.value,
        last_synced_at=row.last_synced_at.isoformat() if row.last_synced_at else None,
    )
