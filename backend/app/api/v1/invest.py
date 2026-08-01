from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import (
    CurrentUser,
    InvestInsightServiceDep,
    InvestServiceDep,
    WatchlistServiceDep,
)
from app.content.invest_explainers import EXPLAINERS
from app.schemas.advice import AdviceOut
from app.schemas.invest import (
    BacktestIn,
    BacktestOut,
    CandleInterval,
    CandleRange,
    CandleSeriesOut,
    CashIn,
    DcaPlanIn,
    DcaPlanOut,
    HoldingIn,
    HoldingOut,
    MarkExecutedIn,
    PortfolioOut,
    ProjectionIn,
    ProjectionOut,
    ProposalOut,
    ProposedOrderOut,
    RebalanceIn,
    RiskOut,
    ScenarioOut,
    SymbolLookupOut,
    SyncOut,
    TargetIn,
    TargetsOut,
    WatchlistItemIn,
    WatchlistItemOut,
)

router = APIRouter(prefix="/invest", tags=["invest"])


# --- watchlist ---
@router.get("/watchlist", response_model=list[WatchlistItemOut])
async def list_watchlist(user: CurrentUser, service: WatchlistServiceDep) -> list[WatchlistItemOut]:
    return await service.list(user.id)


@router.post("/watchlist", response_model=WatchlistItemOut)
async def add_watchlist(
    body: WatchlistItemIn, user: CurrentUser, service: WatchlistServiceDep
) -> WatchlistItemOut:
    return await service.add(user.id, body.symbol)


@router.delete("/watchlist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watchlist(
    item_id: uuid.UUID, user: CurrentUser, service: WatchlistServiceDep
) -> None:
    await service.remove(user.id, item_id)


# --- candles ---
@router.get("/candles", response_model=CandleSeriesOut)
async def get_candles(
    user: CurrentUser,
    service: InvestServiceDep,
    symbol: str = Query(min_length=1, max_length=16),
    interval: CandleInterval = Query("5m"),
    range: CandleRange = Query("1d"),
) -> CandleSeriesOut:
    """OHLC candles for a ticker (TSX: use .TO).

    Free market data, so it's delayed — see ``delayed`` on the response.
    """
    return await service.get_candles(symbol, interval, range)


# --- holdings ---
@router.get("/lookup", response_model=SymbolLookupOut)
async def lookup_symbol(
    user: CurrentUser, service: InvestServiceDep, symbol: str = Query(min_length=1)
) -> SymbolLookupOut:
    """Validate a ticker and return its live price + currency (TSX: use .TO)."""
    return await service.lookup_symbol(symbol)


@router.get("/holdings", response_model=list[HoldingOut])
async def list_holdings(user: CurrentUser, service: InvestServiceDep) -> list[HoldingOut]:
    return await service.list_holdings(user.id)


@router.put("/holdings", response_model=HoldingOut)
async def upsert_holding(
    body: HoldingIn, user: CurrentUser, service: InvestServiceDep
) -> HoldingOut:
    return await service.upsert_holding(user.id, body)


@router.delete("/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holding(
    holding_id: uuid.UUID, user: CurrentUser, service: InvestServiceDep
) -> None:
    await service.delete_holding(user.id, holding_id)


@router.put("/cash")
async def set_cash(body: CashIn, user: CurrentUser, service: InvestServiceDep) -> dict[str, float]:
    return {"cash": await service.set_cash(user.id, body.cash)}


# --- portfolio + data ---
@router.get("/portfolio", response_model=PortfolioOut)
async def portfolio(user: CurrentUser, service: InvestServiceDep) -> PortfolioOut:
    return await service.portfolio(user.id)


@router.post("/sync", response_model=SyncOut)
async def sync(user: CurrentUser, service: InvestServiceDep) -> SyncOut:
    return await service.sync(user.id)


# --- analytics ---
@router.get("/risk", response_model=RiskOut)
async def risk(
    user: CurrentUser,
    service: InvestServiceDep,
    window: str = Query(default="1y", pattern="^(1y|3y|max)$"),
) -> RiskOut:
    return await service.risk(user.id, window)


@router.post("/dca/plan", response_model=DcaPlanOut)
async def dca_plan(body: DcaPlanIn, user: CurrentUser, service: InvestServiceDep) -> DcaPlanOut:
    return await service.dca_plan(user.id, body)


@router.post("/backtest", response_model=BacktestOut)
async def backtest(body: BacktestIn, user: CurrentUser, service: InvestServiceDep) -> BacktestOut:
    return await service.run_backtest(user.id, body)


@router.post("/projection", response_model=ProjectionOut)
async def projection(
    body: ProjectionIn, user: CurrentUser, service: InvestServiceDep
) -> ProjectionOut:
    return await service.projection(user.id, body)


@router.get("/scenarios", response_model=list[ScenarioOut])
async def scenarios(user: CurrentUser, service: InvestServiceDep) -> list[ScenarioOut]:
    return await service.stress_scenarios(user.id)


# --- targets + rebalance checklist ---
@router.get("/targets", response_model=TargetsOut)
async def get_targets(user: CurrentUser, service: InvestServiceDep) -> TargetsOut:
    return await service.get_targets(user.id)


@router.put("/targets", response_model=TargetsOut)
async def set_targets(
    body: list[TargetIn], user: CurrentUser, service: InvestServiceDep
) -> TargetsOut:
    return await service.set_targets(user.id, body)


@router.post("/rebalance/propose", response_model=ProposalOut)
async def propose_rebalance(
    body: RebalanceIn, user: CurrentUser, service: InvestServiceDep
) -> ProposalOut:
    return await service.propose_rebalance(user.id, body.drift_threshold)


@router.get("/orders/proposed", response_model=list[ProposedOrderOut])
async def list_proposed_orders(
    user: CurrentUser,
    service: InvestServiceDep,
    order_status: str | None = Query(default=None, alias="status"),
) -> list[ProposedOrderOut]:
    return await service.list_proposed(user.id, order_status)


@router.post("/orders/proposed/{order_id}/executed", response_model=ProposedOrderOut)
async def mark_executed(
    order_id: uuid.UUID,
    body: MarkExecutedIn,
    user: CurrentUser,
    service: InvestServiceDep,
) -> ProposedOrderOut:
    """The user placed this trade at their own broker — update holdings."""
    return await service.mark_executed(user.id, order_id, body.fill_price)


@router.post("/orders/proposed/{order_id}/reject", response_model=ProposedOrderOut)
async def reject_order(
    order_id: uuid.UUID, user: CurrentUser, service: InvestServiceDep
) -> ProposedOrderOut:
    return await service.reject_order(user.id, order_id)


# --- content + AI ---
@router.get("/explainers")
async def explainers(user: CurrentUser) -> dict[str, dict[str, str]]:
    return EXPLAINERS


@router.get("/insight", response_model=AdviceOut)
async def get_insight(user: CurrentUser, service: InvestInsightServiceDep) -> AdviceOut:
    return await service.get(user.id)


@router.post("/insight/generate", response_model=AdviceOut)
async def generate_insight(
    user: CurrentUser,
    service: InvestInsightServiceDep,
    force: bool = Query(default=False),
) -> AdviceOut:
    return await service.generate(user.id, force=force)
