"""Paper-trading sandbox — simulated buy/sell against fake CAD cash.

Entirely separate from the user's real, recommend-only Holdings. No real orders
are ever placed. Everything is normalized to CAD (US tickers priced via the
cached USD/CAD rate), matching the Invest module.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaperTrade
from app.integrations.marketdata.provider import MarketDataError, MarketDataProvider
from app.repositories.paper import PaperRepository
from app.schemas.paper import (
    PaperAccountOut,
    PaperPortfolioOut,
    PaperPositionOut,
    PaperTradeOut,
)
from app.services.errors import ValidationFailed
from app.services.market_data_service import MarketDataService


class PaperBrokerService:
    def __init__(self, session: AsyncSession, provider: MarketDataProvider) -> None:
        self.session = session
        self.provider = provider
        self.market_data = MarketDataService(session, provider)
        self.repo = PaperRepository(session)

    async def price_cad(self, symbol: str) -> float | None:
        """Live price converted to CAD, or None if it can't be priced."""
        try:
            quote = await self.provider.get_quote(symbol)
        except MarketDataError:
            return None
        if quote.currency != "CAD":
            fx = await self.market_data.latest_fx_rate()
            if not fx:
                await self.market_data.sync_bars([symbol])  # also caches USD/CAD
                fx = await self.market_data.latest_fx_rate()
            if not fx:
                return None
            return round(quote.price * fx, 4)
        return round(quote.price, 4)

    async def portfolio(self, user_id: uuid.UUID) -> PaperPortfolioOut:
        acct = await self.repo.get_or_create_account(user_id)
        positions = await self.repo.list_positions(user_id)
        out: list[PaperPositionOut] = []
        invested = 0.0
        unrealized = 0.0
        stale: list[str] = []

        for pos in positions:
            avg = pos.avg_price_cents / 100
            price = await self.price_cad(pos.symbol)
            if price is None:
                stale.append(pos.symbol)
                out.append(
                    PaperPositionOut(
                        symbol=pos.symbol,
                        qty=pos.qty,
                        avg_price=round(avg, 2),
                        price=None,
                        market_value=0.0,
                        unrealized_pl=0.0,
                        unrealized_pl_pct=0.0,
                        opened_by=pos.opened_by,
                    )
                )
                continue
            mv = round(price * pos.qty, 2)
            pl = round(mv - avg * pos.qty, 2)
            plp = round(price / avg - 1, 4) if avg else 0.0
            invested += mv
            unrealized += pl
            out.append(
                PaperPositionOut(
                    symbol=pos.symbol,
                    qty=pos.qty,
                    avg_price=round(avg, 2),
                    price=price,
                    market_value=mv,
                    unrealized_pl=pl,
                    unrealized_pl_pct=plp,
                    opened_by=pos.opened_by,
                )
            )

        cash = acct.cash_cents / 100
        await self.session.commit()  # account may have just been created
        return PaperPortfolioOut(
            account=PaperAccountOut(
                cash=round(cash, 2),
                invested=round(invested, 2),
                equity=round(cash + invested, 2),
                unrealized_pl=round(unrealized, 2),
            ),
            positions=out,
            stale_symbols=stale,
        )

    async def buy(
        self,
        user_id: uuid.UUID,
        symbol: str,
        qty: float,
        *,
        source: str = "user",
        note: str | None = None,
    ) -> PaperTradeOut:
        sym = symbol.strip().upper()
        price = await self.price_cad(sym)
        if price is None:
            raise ValidationFailed(f"Couldn't price '{sym}'. Check the symbol.")
        cost_cents = round(price * qty * 100)
        acct = await self.repo.get_or_create_account(user_id)
        if cost_cents > acct.cash_cents:
            raise ValidationFailed(
                f"Not enough paper cash: need {price * qty:,.2f} CAD, "
                f"have {acct.cash_cents / 100:,.2f}."
            )
        acct.cash_cents -= cost_cents

        pos = await self.repo.get_position(user_id, sym)
        if pos is None:
            await self.repo.add_position(user_id, sym, qty, round(price * 100), source)
        else:
            total_cost = pos.avg_price_cents * pos.qty + price * 100 * qty
            pos.qty += qty
            pos.avg_price_cents = round(total_cost / pos.qty)

        trade = await self.repo.add_trade(
            user_id=user_id,
            symbol=sym,
            side="buy",
            qty=qty,
            price_cents=round(price * 100),
            source=source,
            note=note,
        )
        await self.session.commit()
        return self._trade_out(trade)

    async def sell(
        self,
        user_id: uuid.UUID,
        symbol: str,
        qty: float,
        *,
        source: str = "user",
        note: str | None = None,
    ) -> PaperTradeOut:
        sym = symbol.strip().upper()
        pos = await self.repo.get_position(user_id, sym)
        if pos is None or pos.qty + 1e-9 < qty:
            raise ValidationFailed(f"You don't hold {qty} shares of {sym} in the paper account.")
        price = await self.price_cad(sym)
        if price is None:
            raise ValidationFailed(f"Couldn't price '{sym}'. Check the symbol.")

        acct = await self.repo.get_or_create_account(user_id)
        acct.cash_cents += round(price * qty * 100)
        pos.qty -= qty
        if pos.qty <= 1e-9:
            await self.repo.delete_position(pos)

        trade = await self.repo.add_trade(
            user_id=user_id,
            symbol=sym,
            side="sell",
            qty=qty,
            price_cents=round(price * 100),
            source=source,
            note=note,
        )
        await self.session.commit()
        return self._trade_out(trade)

    async def trades(self, user_id: uuid.UUID) -> list[PaperTradeOut]:
        return [self._trade_out(t) for t in await self.repo.list_trades(user_id)]

    @staticmethod
    def _trade_out(t: PaperTrade) -> PaperTradeOut:
        return PaperTradeOut(
            id=str(t.id),
            symbol=t.symbol,
            side=t.side,
            qty=t.qty,
            price=round(t.price_cents / 100, 2),
            source=t.source,
            note=t.note,
            created_at=t.created_at.isoformat(),
        )
