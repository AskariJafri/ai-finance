"""Freshest licensed price we can get for a symbol, at the moment it is needed.

Order of preference:

1. **The user's own brokerage, through SnapTrade.** Real time (for most
   brokers), and under the broker's licence for its own customer — the only
   free real-time source that is safe to ship in a product.
2. **The market-data provider (Yahoo).** Fetched fresh at call time, never
   from cache, with the exchange's own timestamp. Near real time for US
   listings, typically ~15 minutes delayed for the TSX. Unofficial: fine for
   development and demos, to be replaced by a licensed feed before a sale.

The broker endpoint is not a market-data feed. SnapTrade caps it at ten
symbols a call, can disable keys that poll it, and leaves it off on free plans
unless their support enables it. So this is called once per user action (a
plan), in as few calls as the cap allows, and any failure quietly falls
through to the provider. It is never polled.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenCipher
from app.core.logging import get_logger
from app.domain.enums import BrokerageConnectionStatus
from app.integrations.marketdata.provider import SymbolQuote
from app.integrations.snaptrade.gateway import (
    MAX_QUOTES_PER_CALL,
    SnapTradeError,
    SnapTradeGateway,
)
from app.repositories.brokerage_connections import BrokerageConnectionRepository
from app.repositories.user_settings import UserSettingsRepository

logger = get_logger(__name__)

#: Suffixes of non-US listings that quote in CAD. SnapTrade's quote carries no
#: currency, and these are the Canadian venues the app maps tickers to.
_CAD_SUFFIXES = (".TO", ".V", ".CN", ".NE")

QuoteFetcher = Callable[[list[str]], Awaitable[dict[str, SymbolQuote]]]


def _currency(symbol: str) -> str:
    return "CAD" if symbol.upper().endswith(_CAD_SUFFIXES) else "USD"


async def broker_quote_fetcher(
    session: AsyncSession,
    user_id,
    gateway: SnapTradeGateway | None,
    cipher: TokenCipher | None,
) -> QuoteFetcher | None:
    """A function that quotes symbols through this user's brokerage, or None
    when they have no usable link.

    Credentials and the account are resolved here, up front, so the returned
    function touches no database session — it can run alongside other work.
    """
    if gateway is None or cipher is None:
        return None
    try:
        row = await UserSettingsRepository(session).get_for_user(user_id)
        if row is None or not row.snaptrade_user_id or not row.snaptrade_secret_encrypted:
            return None
        connections = await BrokerageConnectionRepository(session).list_for_user(user_id)
        account = next(
            (c for c in connections if c.status == BrokerageConnectionStatus.ACTIVE), None
        )
        if account is None:
            return None
        snaptrade_user_id = row.snaptrade_user_id
        secret = cipher.decrypt(row.snaptrade_secret_encrypted)
        account_id = account.brokerage_account_id
        institution = account.institution_name or "your broker"
    except Exception as exc:  # noqa: BLE001 — no broker quotes is not an error
        logger.warning("Broker quotes unavailable: %s", exc)
        return None

    async def fetch(symbols: list[str]) -> dict[str, SymbolQuote]:
        wanted = list(dict.fromkeys(s.upper() for s in symbols))
        chunks = [
            wanted[i : i + MAX_QUOTES_PER_CALL] for i in range(0, len(wanted), MAX_QUOTES_PER_CALL)
        ]

        async def one(chunk: list[str]):
            try:
                return await gateway.get_quotes(
                    snaptrade_user_id=snaptrade_user_id,
                    user_secret=secret,
                    account_id=account_id,
                    tickers=chunk,
                )
            except SnapTradeError as exc:
                logger.warning("Broker quote call failed, using market data: %s", exc)
                return []

        now = datetime.now(UTC)
        out: dict[str, SymbolQuote] = {}
        for quotes in await asyncio.gather(*(one(c) for c in chunks)):
            for q in quotes:
                if q.symbol in wanted:
                    out[q.symbol] = SymbolQuote(
                        symbol=q.symbol,
                        price=q.price,
                        currency=_currency(q.symbol),
                        as_of=now,
                        source=f"broker:{institution}",
                    )
        return out

    return fetch
