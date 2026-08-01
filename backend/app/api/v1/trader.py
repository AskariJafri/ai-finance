from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import (
    AutoTraderServiceDep,
    CommitteeServiceDep,
    CurrentUser,
    DeployServiceDep,
    PaperBrokerServiceDep,
)
from app.schemas.committee import AnalyzeIn, CommitteeResult, SessionSummary
from app.schemas.invest import (
    DeployAcceptIn,
    DeployPlanIn,
    DeployPlanOut,
    ProposalOut,
)
from app.schemas.paper import (
    AutoTraderConfigIn,
    AutoTraderConfigOut,
    AutoTradeRunOut,
    PaperPortfolioOut,
    PaperTradeOut,
    TradeIn,
)

router = APIRouter(prefix="/invest", tags=["trader"])


# --- AI committee ---
@router.post("/committee/analyze", response_model=CommitteeResult)
async def analyze(
    body: AnalyzeIn, user: CurrentUser, service: CommitteeServiceDep
) -> CommitteeResult:
    return await service.analyze(user.id, body.symbol)


@router.get("/committee/sessions", response_model=list[SessionSummary])
async def list_sessions(user: CurrentUser, service: CommitteeServiceDep) -> list[SessionSummary]:
    return await service.list_sessions(user.id)


@router.get("/committee/sessions/{session_id}", response_model=CommitteeResult)
async def get_session(
    session_id: uuid.UUID, user: CurrentUser, service: CommitteeServiceDep
) -> CommitteeResult:
    return await service.get_session(user.id, session_id)


# --- deploy cash ("I have $X to invest") ---
@router.post("/deploy/propose", response_model=DeployPlanOut)
async def propose_deploy(
    body: DeployPlanIn, user: CurrentUser, service: DeployServiceDep
) -> DeployPlanOut:
    """Slow — the committee deliberates on each shortlisted ticker. Persists nothing."""
    return await service.plan(user.id, body)


@router.post("/deploy/accept", response_model=ProposalOut)
async def accept_deploy(
    body: DeployAcceptIn, user: CurrentUser, service: DeployServiceDep
) -> ProposalOut:
    return await service.accept(user.id, body)


# --- paper trading ---
@router.get("/paper/portfolio", response_model=PaperPortfolioOut)
async def paper_portfolio(user: CurrentUser, service: PaperBrokerServiceDep) -> PaperPortfolioOut:
    return await service.portfolio(user.id)


@router.post("/paper/buy", response_model=PaperTradeOut)
async def paper_buy(
    body: TradeIn, user: CurrentUser, service: PaperBrokerServiceDep
) -> PaperTradeOut:
    return await service.buy(user.id, body.symbol, body.qty)


@router.post("/paper/sell", response_model=PaperTradeOut)
async def paper_sell(
    body: TradeIn, user: CurrentUser, service: PaperBrokerServiceDep
) -> PaperTradeOut:
    return await service.sell(user.id, body.symbol, body.qty)


@router.get("/paper/trades", response_model=list[PaperTradeOut])
async def paper_trades(user: CurrentUser, service: PaperBrokerServiceDep) -> list[PaperTradeOut]:
    return await service.trades(user.id)


# --- auto-trader ---
@router.get("/autotrader/config", response_model=AutoTraderConfigOut)
async def autotrader_config(
    user: CurrentUser, service: AutoTraderServiceDep
) -> AutoTraderConfigOut:
    return await service.get_config(user.id)


@router.put("/autotrader/config", response_model=AutoTraderConfigOut)
async def update_autotrader_config(
    body: AutoTraderConfigIn, user: CurrentUser, service: AutoTraderServiceDep
) -> AutoTraderConfigOut:
    return await service.update_config(user.id, body)


@router.post("/autotrader/run", response_model=AutoTradeRunOut)
async def run_autotrader(user: CurrentUser, service: AutoTraderServiceDep) -> AutoTradeRunOut:
    return await service.run(user.id)


@router.get("/autotrader/runs", response_model=list[AutoTradeRunOut])
async def autotrader_runs(
    user: CurrentUser, service: AutoTraderServiceDep
) -> list[AutoTradeRunOut]:
    return await service.runs(user.id)
