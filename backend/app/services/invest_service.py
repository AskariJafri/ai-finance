"""Invest orchestrator: portfolio, risk, DCA, backtests, projections,
scenarios, and the recommend-only rebalance checklist.

Holdings are either user-entered or mirrored from a brokerage account linked
through SnapTrade (see ``brokerage_service``). Prices come from public market
data (Yahoo), so Canadian listings like VFV.TO work natively. Everything is
normalized to CAD for display since the user lives in CAD.

Guardrails: all figures from math over market data (never the LLM); the engine
only ever PROPOSES trades — nothing here may set APPROVED. The user either
executes at their own broker and marks the order done, or approves it for live
submission via ``BrokerageService.approve_and_submit``. Everything carries
INVEST_DISCLAIMER.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import timedelta

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import backtest as bt
from app.analytics import invest_math as im
from app.analytics import montecarlo, scenarios
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Holding, ProposedOrder
from app.domain.enums import OrderSide, ProposedOrderStatus
from app.integrations.marketdata.provider import MarketDataError, MarketDataProvider
from app.repositories.holdings import HoldingRepository
from app.repositories.portfolio_targets import PortfolioTargetRepository
from app.repositories.proposed_orders import ProposedOrderRepository
from app.repositories.user_settings import UserSettingsRepository
from app.schemas.common import cents_to_dollars
from app.schemas.invest import (
    AccountOut,
    BacktestIn,
    BacktestMetrics,
    BacktestOut,
    CandleOut,
    CandleSeriesOut,
    Concentration,
    CorrelationOut,
    DataQuality,
    DcaBacktestOut,
    DcaPlanIn,
    DcaPlanOut,
    DcaProjectionPoint,
    HoldingIn,
    HoldingOut,
    PortfolioOut,
    PortfolioRisk,
    PositionRisk,
    ProjectionIn,
    ProjectionOut,
    ProposalOut,
    ProposedOrderOut,
    RiskOut,
    ScenarioImpact,
    ScenarioOut,
    ScenarioPositionImpact,
    SeriesPoint,
    Sizing,
    SymbolLookupOut,
    SyncOut,
    TargetIn,
    TargetsOut,
)
from app.services.errors import NotFound, ValidationFailed
from app.services.market_data_service import BENCHMARK, MarketDataService

logger = get_logger(__name__)

_WINDOW_DAYS = {"1y": 365, "3y": 365 * 3, "max": 365 * 30}


class InvestService:
    def __init__(self, session: AsyncSession, provider: MarketDataProvider) -> None:
        self.session = session
        self.holdings = HoldingRepository(session)
        self.targets = PortfolioTargetRepository(session)
        self.proposals = ProposedOrderRepository(session)
        self.user_settings = UserSettingsRepository(session)
        self.market_data = MarketDataService(session, provider)

    # --- candles ---
    async def get_candles(self, symbol: str, interval: str, range_: str) -> CandleSeriesOut:
        try:
            series = await self.market_data.get_candles(symbol, interval, range_)
        except MarketDataError as exc:
            # The underlying error carries the upstream URL and an HTTP-status
            # doc link — useful in logs, noise (and a leaked implementation
            # detail) in a message shown on a phone.
            logger.warning("Candle fetch failed for %s: %s", symbol, exc)
            raise ValidationFailed(
                f"Couldn't chart '{symbol.upper()}'. Check the ticker — Canadian "
                "listings need '.TO' (e.g. VFV.TO)."
            ) from exc
        return CandleSeriesOut(
            symbol=series.symbol,
            name=series.name,
            currency=series.currency,
            interval=series.interval,
            range=range_,
            exchange_timezone=series.exchange_timezone,
            market_state=series.market_state,
            previous_close=series.previous_close,
            candles=[
                CandleOut(
                    time=c.time.isoformat(),
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=c.volume,
                )
                for c in series.candles
            ],
        )

    # --- holdings ---
    async def lookup_symbol(self, symbol: str) -> SymbolLookupOut:
        try:
            quote = await self.market_data.lookup(symbol)
        except MarketDataError as exc:
            raise ValidationFailed(
                f"Couldn't find '{symbol.upper()}'. For Canadian listings add "
                f"'.TO' (e.g. VFV.TO). ({exc})"
            ) from exc
        return SymbolLookupOut(
            symbol=quote.symbol,
            name=quote.name,
            price=round(quote.price, 4),
            currency=quote.currency,
        )

    async def list_holdings(self, user_id: uuid.UUID) -> list[HoldingOut]:
        rows = await self.holdings.list_for_user(user_id)
        return [_holding_out(h) for h in rows]

    async def upsert_holding(self, user_id: uuid.UUID, body: HoldingIn) -> HoldingOut:
        quote = await self.lookup_symbol(body.symbol)
        existing = await self.holdings.get(user_id, quote.symbol)
        if existing is not None and existing.is_synced:
            # The next sync would overwrite the edit from the broker, so accept
            # it silently would be a lie. Fix the position at the broker instead.
            raise ValidationFailed(
                f"{quote.symbol} is synced from your linked brokerage account and "
                "can't be edited by hand. Trade it at your broker, then sync."
            )
        row = await self.holdings.upsert(
            user_id=user_id,
            symbol=quote.symbol,
            qty=body.qty,
            avg_cost_cents=round(body.avg_cost * 100),
            currency=quote.currency,
            name=quote.name,
        )
        await self.market_data.sync_bars([quote.symbol])
        await self.session.commit()
        return _holding_out(row)

    async def delete_holding(self, user_id: uuid.UUID, holding_id: uuid.UUID) -> None:
        if not await self.holdings.delete(user_id, holding_id):
            raise NotFound("Holding not found.")
        await self.session.commit()

    async def set_cash(self, user_id: uuid.UUID, cash: float) -> float:
        row = await self.user_settings.get_for_user(user_id)
        if row is None:
            row = await self.user_settings.set_income_override(user_id, None)
        row.invest_cash_cents = round(cash * 100)
        await self.session.commit()
        return cents_to_dollars(row.invest_cash_cents)

    async def _cash_cents(self, user_id: uuid.UUID) -> int:
        row = await self.user_settings.get_for_user(user_id)
        return row.invest_cash_cents if row else 0

    # --- portfolio (holdings x latest prices, normalized to CAD) ---
    async def portfolio(self, user_id: uuid.UUID) -> PortfolioOut:
        rows = await self.holdings.list_for_user(user_id)
        if not rows:
            return PortfolioOut(connected=False)

        fx = await self.market_data.latest_fx_rate() or 1.0
        cash = cents_to_dollars(await self._cash_cents(user_id))

        priced: list[dict] = []
        stale: list[str] = []
        for h in rows:
            close = await self.market_data.latest_close(h.symbol)
            if close is None:
                stale.append(h.symbol)
                continue
            to_cad = fx if h.currency == "USD" else 1.0
            market_value = close * h.qty * to_cad
            cost = cents_to_dollars(h.avg_cost_cents) * h.qty * to_cad
            priced.append(
                {
                    "symbol": h.symbol,
                    "name": h.name,
                    "qty": h.qty,
                    "currency": h.currency,
                    # Lets the app label a row as coming from the linked broker
                    # rather than hand-entered; only /invest/holdings knew this.
                    "is_synced": h.is_synced,
                    "price": close,
                    "avg_cost": cents_to_dollars(h.avg_cost_cents),
                    "market_value": market_value,
                    "unrealized_pl": market_value - cost,
                    "unrealized_pl_pct": ((market_value / cost - 1.0) if cost else 0.0),
                }
            )

        invested = sum(p["market_value"] for p in priced)
        total = invested + cash
        return PortfolioOut(
            connected=True,
            as_of=date_type.today().isoformat(),
            currency="CAD",
            usd_cad_rate=round(fx, 4),
            account=AccountOut(
                equity=round(total, 2), cash=round(cash, 2), invested=round(invested, 2)
            ),
            positions=[
                {
                    **p,
                    "market_value": round(p["market_value"], 2),
                    "unrealized_pl": round(p["unrealized_pl"], 2),
                    "unrealized_pl_pct": round(p["unrealized_pl_pct"], 4),
                    "weight": round(p["market_value"] / invested, 4) if invested else 0.0,
                }
                for p in priced
            ],
            stale_symbols=stale,
        )

    async def sync(self, user_id: uuid.UUID) -> SyncOut:
        rows = await self.holdings.list_for_user(user_id)
        symbols = [h.symbol for h in rows]
        updated, added, failed = await self.market_data.sync_bars(symbols)
        await self.session.commit()
        start = await self.market_data.data_start(symbols + [BENCHMARK]) if symbols else None
        return SyncOut(
            symbols_updated=updated,
            bars_added=added,
            data_start=start.isoformat() if start else None,
            failed_symbols=failed,
        )

    # --- shared analytics helpers ---
    async def _weights_and_value(self, user_id: uuid.UUID) -> tuple[dict[str, float], float]:
        pf = await self.portfolio(user_id)
        if not pf.connected or not pf.positions:
            raise ValidationFailed(
                "Add your holdings first (Invest → Holdings) so we know what to analyze."
            )
        invested = sum(p["market_value"] for p in pf.positions)
        weights = {p["symbol"]: p["market_value"] / invested for p in pf.positions}
        return weights, invested

    async def _aligned_returns(
        self, symbols: list[str], start: date_type | None, end: date_type | None = None
    ) -> tuple[list[date_type], dict[str, np.ndarray], list[str]]:
        series = await self.market_data.get_close_series(symbols, start=start, end=end)
        excluded = [s for s, pts in series.items() if len(pts) < 2]
        series = {s: pts for s, pts in series.items() if len(pts) >= 2}
        dates, closes = im.align_series(series)
        returns = {s: im.returns_from_closes(c) for s, c in closes.items()}
        return dates, returns, excluded

    # --- risk ---
    async def risk(self, user_id: uuid.UUID, window: str = "1y") -> RiskOut:
        weights, total_value = await self._weights_and_value(user_id)
        start = date_type.today() - timedelta(days=_WINDOW_DAYS.get(window, 365))
        dates, returns, excluded = await self._aligned_returns(list(weights) + [BENCHMARK], start)

        held = [s for s in weights if s in returns]
        quality = DataQuality(
            days_used=len(dates) - 1 if dates else 0,
            start=dates[0].isoformat() if dates else None,
            end=dates[-1].isoformat() if dates else None,
            symbols_excluded=excluded,
        )
        if not held or quality.days_used < im.MIN_DAYS:
            return RiskOut(
                window=window,
                portfolio=PortfolioRisk(),
                risk_free_rate=settings.risk_free_rate,
                data_quality=quality,
                reason=(
                    "Not enough overlapping price history yet — tap Sync data "
                    f"(need {im.MIN_DAYS}+ trading days)."
                ),
            )

        wsum = sum(weights[s] for s in held)
        w = {s: weights[s] / wsum for s in held}
        r_p = im.portfolio_returns(w, {s: returns[s] for s in held})
        rf = settings.risk_free_rate

        var95 = im.hist_var_cvar(r_p, 0.95)
        var99 = im.hist_var_cvar(r_p, 0.99)
        bench = returns.get(BENCHMARK)

        weights_list = [w[s] for s in held]
        top_symbol = max(w, key=w.get)
        corr_symbols, corr_matrix = im.correlation_matrix({s: returns[s] for s in held})
        return RiskOut(
            window=window,
            portfolio=PortfolioRisk(
                ann_vol=_r4(im.annualized_vol(r_p)),
                sharpe=_r4(im.sharpe(r_p, rf)),
                sortino=_r4(im.sortino(r_p, rf)),
                max_drawdown=_r4(im.max_drawdown(r_p)),
                beta=_r4(im.beta(r_p, bench)) if bench is not None else None,
                var_95=_r4(var95[0]) if var95 else None,
                var_99=_r4(var99[0]) if var99 else None,
                cvar_95=_r4(var95[1]) if var95 else None,
                var_95_amount=round(var95[0] * total_value, 2) if var95 else None,
                var_99_amount=round(var99[0] * total_value, 2) if var99 else None,
                cvar_95_amount=round(var95[1] * total_value, 2) if var95 else None,
            ),
            concentration=Concentration(
                hhi=round(im.hhi(weights_list), 4),
                effective_n=round(1.0 / im.hhi(weights_list), 2),
                top_symbol=top_symbol,
                top_weight=round(w[top_symbol], 4),
            ),
            per_position=[
                PositionRisk(
                    symbol=s,
                    weight=round(w[s], 4),
                    ann_vol=_r4(im.annualized_vol(returns[s])),
                    beta=_r4(im.beta(returns[s], bench)) if bench is not None else None,
                    max_drawdown=_r4(im.max_drawdown(returns[s])),
                )
                for s in held
            ],
            correlation=CorrelationOut(symbols=corr_symbols, matrix=corr_matrix),
            risk_free_rate=rf,
            data_quality=quality,
        )

    # --- dca ---
    async def dca_plan(self, user_id: uuid.UUID, body: DcaPlanIn) -> DcaPlanOut:
        weights = {a.symbol.upper(): a.weight for a in body.allocations}
        if abs(sum(weights.values()) - 1.0) > 1e-6:
            raise ValidationFailed("Allocation weights must sum to 100%.")
        await self.market_data.sync_bars(list(weights))
        await self.session.commit()

        start = date_type.fromisoformat(body.start_date)
        series = await self.market_data.get_close_series(list(weights), start=start)
        dates, closes = im.align_series({s: pts for s, pts in series.items() if len(pts) >= 2})
        data_start = await self.market_data.data_start(list(weights))

        m = (1.0 + body.expected_annual_return) ** (1.0 / 12.0) - 1.0
        projection = []
        value = 0.0
        for month in range(1, body.horizon_years * 12 + 1):
            value = value * (1.0 + m) + body.monthly_amount
            if month % 3 == 0 or month == 1:
                projection.append(DcaProjectionPoint(month=month, value=round(value, 2)))

        lump_note = (
            "Lump sum usually wins because markets usually rise — this is a "
            "framing comparison, not a recommendation."
        )
        if len(dates) < im.MIN_DAYS:
            return DcaPlanOut(
                backtest=None,
                lump_sum_final_value=None,
                lump_sum_note=lump_note,
                projection=projection,
                data_start=data_start.isoformat() if data_start else None,
                data_quality=DataQuality(days_used=len(dates)),
            )

        missing = [s for s in weights if s not in closes]
        result = bt.dca(dates, closes, weights, body.monthly_amount)
        lump = bt.buy_hold(dates, closes, weights, capital=result.contributed or 1.0)
        step = max(1, len(dates) // 200)
        return DcaPlanOut(
            backtest=DcaBacktestOut(
                contributed=round(result.contributed or 0.0, 2),
                final_value=round(float(result.equity[-1]), 2),
                money_weighted_return=bt.dca_irr(result),
                series=[
                    SeriesPoint(date=dates[i].isoformat(), value=round(float(result.equity[i]), 2))
                    for i in range(0, len(dates), step)
                ],
                contributed_series=_contributed_series(dates, body.monthly_amount, step),
            ),
            lump_sum_final_value=round(float(lump.equity[-1]), 2),
            lump_sum_note=lump_note,
            projection=projection,
            data_start=data_start.isoformat() if data_start else None,
            data_quality=DataQuality(
                days_used=len(dates) - 1,
                start=dates[0].isoformat(),
                end=dates[-1].isoformat(),
                symbols_excluded=missing,
            ),
        )

    # --- backtest ---
    async def run_backtest(self, user_id: uuid.UUID, body: BacktestIn) -> BacktestOut:
        symbols = [s.upper() for s in body.symbols]
        await self.market_data.sync_bars(symbols)
        await self.session.commit()

        start = date_type.fromisoformat(body.start)
        end = date_type.fromisoformat(body.end) if body.end else date_type.today()
        series = await self.market_data.get_close_series(
            symbols + [BENCHMARK], start=start, end=end
        )
        dates, closes = im.align_series({s: pts for s, pts in series.items() if len(pts) >= 2})
        if len(dates) < im.MIN_DAYS:
            raise ValidationFailed(
                f"Not enough overlapping history for {', '.join(symbols)} in that "
                f"range (need {im.MIN_DAYS}+ trading days). Widen the range or "
                "check the tickers."
            )
        missing = [s for s in symbols if s not in closes]
        available = [s for s in symbols if s in closes]
        if not available:
            raise ValidationFailed("No price history for those symbols.")

        if body.weights and len(body.weights) == len(available):
            weights = dict(zip(available, body.weights, strict=True))
            total_w = sum(weights.values())
            weights = {s: w / total_w for s, w in weights.items()}
        else:
            weights = {s: 1.0 / len(available) for s in available}

        rf = settings.risk_free_rate
        if body.strategy == "buy_hold":
            result = bt.buy_hold(dates, closes, weights)
        elif body.strategy == "dca":
            result = bt.dca(dates, closes, weights, body.monthly_amount)
        elif body.strategy == "sma_crossover":
            if body.fast >= body.slow:
                raise ValidationFailed("Fast SMA window must be shorter than slow.")
            result = bt.sma_crossover(
                dates, closes[available[0]], fast=body.fast, slow=body.slow, risk_free=rf
            )
        else:
            result = bt.fixed_mix(dates, closes, weights, rebalance=body.rebalance)

        bench = bt.buy_hold(dates, {BENCHMARK: closes[BENCHMARK]}, {BENCHMARK: 1.0})
        data_start = await self.market_data.data_start(available)
        return BacktestOut(
            strategy=body.strategy,
            equity_curve=[SeriesPoint(**p) for p in bt.downsample(dates, result.equity)],
            benchmark_curve=[SeriesPoint(**p) for p in bt.downsample(dates, bench.equity)],
            metrics=BacktestMetrics(**_round_metrics(bt.summarize(result, rf))),
            benchmark_metrics=BacktestMetrics(**_round_metrics(bt.summarize(bench, rf))),
            data_start=data_start.isoformat() if data_start else None,
            data_quality=DataQuality(
                days_used=len(dates) - 1,
                start=dates[0].isoformat(),
                end=dates[-1].isoformat(),
                symbols_excluded=missing,
            ),
        )

    # --- projection ---
    async def projection(self, user_id: uuid.UUID, body: ProjectionIn) -> ProjectionOut:
        weights, total_value = await self._weights_and_value(user_id)
        start = date_type.today() - timedelta(days=365 * 3)
        dates, returns, excluded = await self._aligned_returns(list(weights) + [BENCHMARK], start)
        held = [s for s in weights if s in returns]
        if not held or (len(dates) - 1) < im.MIN_DAYS:
            raise ValidationFailed(
                "Not enough price history to run a projection — tap Sync data first."
            )
        wsum = sum(weights[s] for s in held)
        w = {s: weights[s] / wsum for s in held}
        r_p = im.portfolio_returns(w, {s: returns[s] for s in held})
        log_r = np.log1p(r_p)

        n_paths = min(body.n_paths or settings.mc_paths_default, settings.mc_paths_max)
        result = montecarlo.project(
            log_r,
            total_value,
            horizon_years=body.horizon_years,
            method=body.method,
            n_paths=n_paths,
            monthly_contribution=body.monthly_contribution,
            seed=body.seed,
        )
        return ProjectionOut(
            start_value=round(total_value, 2),
            months=result.months,
            bands=result.bands,
            prob_loss=result.prob_loss,
            assumptions={
                "mu_ann": result.mu_ann,
                "sigma_ann": result.sigma_ann,
                "method": body.method,
                "n_paths": n_paths,
                "window_days": len(dates) - 1,
            },
            data_quality=DataQuality(
                days_used=len(dates) - 1,
                start=dates[0].isoformat(),
                end=dates[-1].isoformat(),
                symbols_excluded=excluded,
            ),
        )

    # --- scenarios ---
    async def stress_scenarios(self, user_id: uuid.UUID) -> list[ScenarioOut]:
        weights, total_value = await self._weights_and_value(user_id)
        start = date_type.today() - timedelta(days=365 * 3)
        _, returns, _ = await self._aligned_returns(list(weights) + [BENCHMARK], start)
        bench = returns.get(BENCHMARK)

        positions = []
        for symbol, weight in weights.items():
            b = None
            if bench is not None and symbol in returns:
                b = im.beta(returns[symbol], bench)
            positions.append({"symbol": symbol, "market_value": weight * total_value, "beta": b})

        out = []
        for scenario in scenarios.SCENARIOS:
            applied = scenarios.apply_scenario(scenario, positions)
            out.append(
                ScenarioOut(
                    key=scenario.key,
                    name=scenario.name,
                    description=scenario.description,
                    source=scenario.source,
                    portfolio_impact=ScenarioImpact(**applied["portfolio_impact"]),
                    per_position=[ScenarioPositionImpact(**p) for p in applied["per_position"]],
                    index_recovery={
                        "index": scenario.recovery_index,
                        "months": scenario.recovery_months,
                        "note": scenario.recovery_note,
                    },
                )
            )
        return out

    # --- targets + rebalance checklist ---
    async def get_targets(self, user_id: uuid.UUID) -> TargetsOut:
        rows = await self.targets.list_for_user(user_id)
        return TargetsOut(targets=[TargetIn(symbol=r.symbol, weight=r.weight) for r in rows])

    async def set_targets(self, user_id: uuid.UUID, targets: list[TargetIn]) -> TargetsOut:
        symbols = [t.symbol.upper() for t in targets]
        if len(set(symbols)) != len(symbols):
            raise ValidationFailed("Duplicate symbols in targets.")
        if abs(sum(t.weight for t in targets) - 1.0) > 1e-6:
            raise ValidationFailed("Target weights must sum to 100%.")
        await self.targets.replace_all(user_id, [(t.symbol, t.weight) for t in targets])
        await self.session.commit()
        return await self.get_targets(user_id)

    async def propose_rebalance(
        self, user_id: uuid.UUID, drift_threshold: float, sizing: Sizing = "shares"
    ) -> ProposalOut:
        pf = await self.portfolio(user_id)
        if not pf.connected or not pf.positions:
            raise ValidationFailed("Add your holdings first.")
        target_rows = await self.targets.list_for_user(user_id)
        if not target_rows:
            raise ValidationFailed("Set target weights first.")
        targets = {r.symbol: r.weight for r in target_rows}

        # Targets you don't own yet have no cached bars — fetch them, or we
        # could never propose the buy that gets you into the position.
        held_symbols = {p["symbol"] for p in pf.positions}
        new_targets = [s for s in targets if s not in held_symbols]
        if new_targets:
            await self.market_data.sync_bars(new_targets)
            await self.session.commit()

        invested = sum(p["market_value"] for p in pf.positions)
        cash = pf.account.cash if pf.account else 0.0
        account_value = invested + cash
        by_symbol = {p["symbol"]: p for p in pf.positions}

        await self.proposals.expire_open_proposals(user_id)
        batch_id = uuid.uuid4()
        orders: list[ProposedOrder] = []
        # Anything the chosen sizing cannot express. Reported in the note rather
        # than dropped: a position missing from the plan with no explanation
        # reads as "nothing to do here", which is the opposite of the truth.
        skipped: list[str] = []

        for symbol in sorted(set(by_symbol) | set(targets)):
            current_value = by_symbol.get(symbol, {}).get("market_value", 0.0)
            target_w = targets.get(symbol, 0.0)
            target_value = target_w * account_value
            drift = (current_value / account_value if account_value else 0.0) - target_w
            if abs(drift) < drift_threshold:
                continue
            delta = target_value - current_value
            if abs(delta) < 1.0:
                continue

            price_cad = None
            position = by_symbol.get(symbol)
            if position:
                price_cad = position["market_value"] / position["qty"] if position["qty"] else None
            else:
                close = await self.market_data.latest_close(symbol)
                if close is None:
                    continue
                fx = await self.market_data.latest_fx_rate() or 1.0
                # Unheld target: assume it prices like its ticker suffix implies.
                price_cad = close if symbol.endswith(".TO") else close * fx

            if not price_cad:
                continue
            shares = round(abs(delta) / price_cad, 4)
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            if side == OrderSide.SELL and position:
                shares = min(shares, position["qty"])
            if sizing == "shares":
                whole = float(int(shares))
                if side == OrderSide.SELL and position and whole < 1:
                    # A fractional holding can only be sold whole or not at all,
                    # and liquidating one is a normal order at any broker that
                    # sold it to you. Rounding it to zero is how a 0.68-share
                    # position disappeared from every plan.
                    held_qty = position["qty"]
                    if held_qty < 1 and shares >= held_qty - 1e-9:
                        whole = held_qty
                if whole < 1 and not (side == OrderSide.SELL and whole > 0):
                    skipped.append(f"{symbol} (needs {shares:g} share(s) — less than one)")
                    continue
                shares = whole
            dollars = round(shares * price_cad, 2)

            orders.append(
                ProposedOrder(
                    user_id=user_id,
                    batch_id=batch_id,
                    kind="rebalance",
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    qty=None if sizing == "dollars" else shares,
                    notional_cents=round(dollars * 100) if sizing == "dollars" else None,
                    est_value_cents=round(dollars * 100),
                    reason=(
                        f"{symbol} is {abs(drift):.1%} "
                        f"{'above' if drift > 0 else 'below'} its {target_w:.0%} target "
                        + (
                            f"({'sell' if drift > 0 else 'buy'} about ${dollars:,.2f} CAD)."
                            if sizing == "dollars"
                            else f"({'sell' if drift > 0 else 'buy'} {shares:g} share(s) "
                            f"≈ ${dollars:,.2f} CAD)."
                        )
                    ),
                )
            )

        total_buy = sum(
            cents_to_dollars(o.est_value_cents) for o in orders if o.side == OrderSide.BUY
        )
        total_sell = sum(
            cents_to_dollars(o.est_value_cents) for o in orders if o.side == OrderSide.SELL
        )
        await self.proposals.add_all(orders)
        await self.session.commit()

        skipped_note = (
            " Left out because whole shares can't express them: "
            + ", ".join(skipped)
            + ". Switch to dollar amounts to include them."
            if skipped
            else ""
        )
        if not orders:
            note = (
                f"Everything is within {drift_threshold:.0%} of target — no trades needed."
                + skipped_note
            )
        elif sizing == "dollars":
            note = (
                "Dollar amounts, so this needs a broker that sells fractional "
                "shares. Place them at your own broker, then mark each one done "
                "— your holdings update automatically. Sells first, then buys."
            )
        else:
            note = (
                "Place these trades at your own broker, then mark each one done — "
                "your holdings update automatically. Sells first, then buys." + skipped_note
            )
        return ProposalOut(
            batch_id=str(batch_id) if orders else None,
            orders=[order_out(o) for o in orders],
            total_buy=round(total_buy, 2),
            total_sell=round(total_sell, 2),
            note=note,
        )

    async def list_proposed(
        self, user_id: uuid.UUID, status: str | None = None
    ) -> list[ProposedOrderOut]:
        status_enum = ProposedOrderStatus(status) if status else None
        rows = await self.proposals.list_for_user(user_id, status_enum)
        return [order_out(o) for o in rows]

    async def apply_fill(
        self,
        user_id: uuid.UUID,
        order: ProposedOrder,
        *,
        qty: float | None = None,
        fill_price: float | None = None,
    ) -> None:
        """Move holdings to reflect a filled order. Does not commit.

        Shared by the hand-executed path (``mark_executed``) and live broker
        execution, so cost-basis maths lives in exactly one place. ``qty``
        overrides the order quantity for a partial fill.

        For a synced holding the broker is authoritative and the next position
        sync will overwrite this; applying it anyway keeps the portfolio correct
        in the window before that sync runs.

        **Cash moves too.** A trade is an exchange, not a deletion: selling
        converts shares into cash and buying does the reverse. Moving only the
        shares makes every executed sell destroy its own proceeds and every buy
        conjure its cost, so the account total drifts by the size of each trade.
        """
        filled_qty = order.qty if qty is None else qty
        filled_qty = filled_qty or 0.0
        holding = await self.holdings.get(user_id, order.symbol)

        if order.side == OrderSide.SELL:
            if holding is None or holding.qty < filled_qty - 1e-9:
                raise ValidationFailed(
                    f"You don't hold {filled_qty:g} shares of {order.symbol} to sell."
                )
            proceeds = await self._fill_value_cad(order, holding.currency, filled_qty, fill_price)
            remaining = holding.qty - filled_qty
            if remaining <= 1e-9:
                await self.holdings.delete(user_id, holding.id)
            else:
                holding.qty = remaining
            await self._move_cash(user_id, proceeds)
            return

        quote = await self.lookup_symbol(order.symbol)
        price = fill_price if fill_price is not None else quote.price
        await self._move_cash(
            user_id,
            -await self._fill_value_cad(order, quote.currency, filled_qty, price),
        )
        if holding is None:
            await self.holdings.upsert(
                user_id=user_id,
                symbol=order.symbol,
                qty=filled_qty,
                avg_cost_cents=round(price * 100),
                currency=quote.currency,
                name=quote.name,
            )
        else:
            # Weighted-average cost basis.
            old_cost = cents_to_dollars(holding.avg_cost_cents) * holding.qty
            new_qty = holding.qty + filled_qty
            holding.avg_cost_cents = round(((old_cost + price * filled_qty) / new_qty) * 100)
            holding.qty = new_qty

    async def _fill_value_cad(
        self,
        order: ProposedOrder,
        currency: str,
        filled_qty: float,
        fill_price: float | None,
    ) -> float:
        """What the trade was worth, in CAD.

        A dollar-sized order already carries its value; a share-sized one is
        quantity times the fill price, converted like everything else in the
        portfolio.
        """
        if order.notional_cents and filled_qty == (order.qty or 0.0):
            # Notional orders have no share count of their own, so their
            # recorded amount is the value — already CAD, like every figure the
            # planners produce.
            return cents_to_dollars(order.notional_cents)
        if fill_price is None:
            quote = await self.lookup_symbol(order.symbol)
            fill_price = quote.price
            currency = quote.currency
        value = filled_qty * fill_price
        if currency.upper() == "USD":
            value *= await self.market_data.latest_fx_rate() or 1.0
        return value

    async def _move_cash(self, user_id: uuid.UUID, delta_cad: float) -> None:
        """Credit or debit the tracked cash balance. Never below zero.

        A negative cash balance is not a state a cash account can be in — it
        means the recorded figure was stale before the trade, not that the user
        owes money. For a linked account the next sync replaces this with the
        broker's own number anyway.
        """
        row = await self.user_settings.get_or_create(user_id)
        row.invest_cash_cents = max(0, row.invest_cash_cents + round(delta_cad * 100))
        await self.session.flush()

    async def mark_executed(
        self, user_id: uuid.UUID, order_id: uuid.UUID, fill_price: float | None
    ) -> ProposedOrderOut:
        """The user placed this trade at their broker — update holdings to match."""
        order = await self.proposals.get_for_user(user_id, order_id)
        if order is None:
            raise NotFound("Proposed order not found.")
        if order.status != ProposedOrderStatus.PROPOSED:
            raise ValidationFailed(f"Order is already {order.status.value}.")

        await self.apply_fill(user_id, order, fill_price=fill_price)
        order.status = ProposedOrderStatus.FILLED
        await self.session.commit()
        return order_out(order)

    async def reject_order(self, user_id: uuid.UUID, order_id: uuid.UUID) -> ProposedOrderOut:
        order = await self.proposals.get_for_user(user_id, order_id)
        if order is None:
            raise NotFound("Proposed order not found.")
        if order.status != ProposedOrderStatus.PROPOSED:
            raise ValidationFailed(f"Order is already {order.status.value}.")
        order.status = ProposedOrderStatus.REJECTED
        await self.session.commit()
        return order_out(order)

    # --- health-check figures for the AI layer ---
    async def health_figures(self, user_id: uuid.UUID) -> dict:
        risk = await self.risk(user_id, "1y")
        targets = await self.get_targets(user_id)
        portfolio = await self.portfolio(user_id)
        target_map = {t.symbol: t.weight for t in targets.targets}
        drifts = []
        for p in portfolio.positions:
            target = target_map.get(p["symbol"])
            if target is not None:
                drift = p["weight"] - target
                if abs(drift) > 0.05:
                    drifts.append(
                        {
                            "symbol": p["symbol"],
                            "weight": p["weight"],
                            "target": target,
                            "drift": round(drift, 4),
                        }
                    )
        vol = risk.portfolio.ann_vol
        bucket = None
        if vol is not None:
            bucket = "conservative" if vol < 0.10 else "balanced" if vol <= 0.18 else "aggressive"

        equity = portfolio.account.equity if portfolio.account else 0.0
        cash = portfolio.account.cash if portfolio.account else 0.0
        invested = portfolio.account.invested if portfolio.account else 0.0

        # A CAD investor holding US-listed names carries currency risk on top of
        # market risk, and it is invisible in the weights alone — every figure
        # here is already converted to CAD.
        usd_value = sum(
            p["market_value"] for p in portfolio.positions if p.get("currency") == "USD"
        )

        positions = [
            {
                "symbol": p["symbol"],
                "weight": p["weight"],
                "market_value": p["market_value"],
                "unrealized_pl_pct": p["unrealized_pl_pct"],
                "currency": p["currency"],
            }
            for p in sorted(portfolio.positions, key=lambda p: p["market_value"], reverse=True)
        ]

        return {
            "equity": equity,
            "currency": "CAD",
            "n_positions": len(portfolio.positions),
            "positions": positions,
            "cash": round(cash, 2),
            "cash_pct": round(cash / equity, 4) if equity else 0.0,
            "usd_exposure_pct": round(usd_value / invested, 4) if invested else 0.0,
            "from_brokerage": any(p.get("is_synced") for p in portfolio.positions),
            "risk": risk.portfolio.model_dump(),
            "risk_bucket": bucket,
            "concentration": risk.concentration.model_dump() if risk.concentration else None,
            "drifted_positions": drifts,
            "has_targets": bool(target_map),
        }


def _r4(v: float | None) -> float | None:
    return round(v, 4) if v is not None else None


def _round_metrics(m: dict) -> dict:
    return {k: (_r4(v) if isinstance(v, float) else v) for k, v in m.items()}


def _contributed_series(dates, monthly_amount: float, step: int) -> list[SeriesPoint]:
    values = []
    contributed = 0.0
    prev_month = None
    for d in dates:
        month = (d.year, d.month)
        if month != prev_month:
            prev_month = month
            contributed += monthly_amount
        values.append(contributed)
    return [
        SeriesPoint(date=dates[i].isoformat(), value=round(values[i], 2))
        for i in range(0, len(dates), step)
    ]


def _holding_out(h: Holding) -> HoldingOut:
    return HoldingOut(
        id=str(h.id),
        symbol=h.symbol,
        name=h.name,
        qty=h.qty,
        avg_cost=cents_to_dollars(h.avg_cost_cents),
        currency=h.currency,
        is_synced=h.is_synced,
    )


def order_out(o: ProposedOrder) -> ProposedOrderOut:
    return ProposedOrderOut(
        id=str(o.id),
        batch_id=str(o.batch_id),
        kind=o.kind,
        symbol=o.symbol,
        side=o.side.value,
        qty=o.qty,
        notional=cents_to_dollars(o.notional_cents) if o.notional_cents else None,
        est_value=cents_to_dollars(o.est_value_cents),
        reason=o.reason,
        status=o.status.value,
        created_at=o.created_at.isoformat(),
        broker_order_id=o.broker_order_id,
        status_detail=o.status_detail,
    )
