"""Watchlist — tickers the user tracks without holding, shown with live quotes.

Reuses the same market-data provider and daily-bar cache as the Invest module,
so Canadian (VFV.TO) and US (SPY) tickers both work, priced from Yahoo. A bad or
unreachable ticker never breaks the list — that row is returned ``stale``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WatchlistItem
from app.integrations.marketdata.provider import MarketDataError, MarketDataProvider
from app.repositories.watchlist import WatchlistRepository
from app.schemas.invest import WatchlistItemOut
from app.services.errors import ValidationFailed
from app.services.market_data_service import MarketDataService


class WatchlistService:
    def __init__(self, session: AsyncSession, provider: MarketDataProvider) -> None:
        self.session = session
        self.repo = WatchlistRepository(session)
        self.provider = provider
        self.market_data = MarketDataService(session, provider)

    async def list(self, user_id: uuid.UUID) -> list[WatchlistItemOut]:
        items = await self.repo.list_for_user(user_id)
        return list(await asyncio.gather(*(self._enrich(i) for i in items)))

    async def _enrich(self, item: WatchlistItem) -> WatchlistItemOut:
        price: float | None = None
        currency = "USD"
        name = item.name
        change_pct: float | None = None
        stale = False

        try:
            quote = await self.provider.get_quote(item.symbol)
            price = round(quote.price, 4)
            currency = quote.currency
            name = quote.name or item.name
        except MarketDataError:
            stale = True

        if price is not None:
            change_pct = await self._day_change(item.symbol, price)

        return WatchlistItemOut(
            id=str(item.id),
            symbol=item.symbol,
            name=name,
            price=price,
            currency=currency,
            change_pct=change_pct,
            stale=stale,
        )

    async def _day_change(self, symbol: str, price: float) -> float | None:
        """Percent move vs the previous session's close (from the bar cache)."""
        series = (await self.market_data.get_close_series([symbol])).get(symbol.upper(), [])
        today = date.today()
        # The most recent close strictly before today = the prior session close.
        prev_close = next((close for d, close in reversed(series) if d < today and close), None)
        if prev_close is None:
            return None
        return round((price - prev_close) / prev_close, 4)

    async def add(self, user_id: uuid.UUID, symbol: str) -> WatchlistItemOut:
        sym = symbol.strip().upper()
        try:
            quote = await self.provider.get_quote(sym)
        except MarketDataError as exc:
            raise ValidationFailed(f"Couldn't find a ticker '{sym}'. Check the symbol.") from exc

        item = await self.repo.get(user_id, sym) or await self.repo.add(user_id, sym, quote.name)
        # Warm the bar cache so day-change works on the next list.
        await self.market_data.sync_bars([sym])
        await self.session.commit()
        return WatchlistItemOut(
            id=str(item.id),
            symbol=sym,
            name=quote.name,
            price=round(quote.price, 4),
            currency=quote.currency,
            change_pct=await self._day_change(sym, quote.price),
            stale=False,
        )

    async def remove(self, user_id: uuid.UUID, item_id: uuid.UUID) -> None:
        await self.repo.delete(user_id, item_id)
        await self.session.commit()
