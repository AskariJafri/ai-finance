from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.factory import get_advice_llm_provider
from app.api.deps import (
    AutoTraderServiceDep,
    CommitteeServiceDep,
    CurrentUser,
    DeployServiceDep,
    PaperBrokerServiceDep,
    PortfolioActionsServiceDep,
    SessionDep,
)
from app.core.config import settings
from app.core.crypto import get_cipher
from app.core.logging import get_logger
from app.db.models import ActionPlanRun
from app.integrations.marketdata.factory import get_market_data_provider
from app.integrations.news.factory import get_news_provider
from app.integrations.snaptrade.factory import get_snaptrade_gateway
from app.integrations.websearch.factory import get_websearch_provider
from app.repositories.action_runs import ActionRunRepository, market_period
from app.repositories.holdings import HoldingRepository
from app.repositories.user_settings import UserSettingsRepository
from app.schemas.committee import AnalyzeIn, CommitteeResult, SessionSummary
from app.schemas.invest import (
    ActionAcceptIn,
    ActionPlanIn,
    ActionPlanOut,
    ActionRunIn,
    ActionRunOut,
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
from app.services.errors import NotFound, ServiceError  # noqa: E402
from app.services.market_context_service import MarketContextService  # noqa: E402
from app.services.portfolio_actions_service import PortfolioActionsService  # noqa: E402

router = APIRouter(prefix="/invest", tags=["trader"])
logger = get_logger(__name__)


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


# --- AI portfolio actions (sell + buy over what you already own) ---
@router.post("/actions/propose", response_model=ActionPlanOut)
async def propose_actions(
    body: ActionPlanIn, user: CurrentUser, service: PortfolioActionsServiceDep
) -> ActionPlanOut:
    """Slow — one committee deliberation per held position and per shortlisted
    candidate. Read-only: persists nothing and can never place a trade."""
    return await service.plan(user.id, body)


# --- the same plan, as a background run the app polls ---
#
# A plan takes one to three minutes. As one request it was hostage to the
# screen that sent it: back out, switch tabs or lock the phone and the answer
# was gone. A run is a row; the work carries on without the client, and any
# screen can pick the result up later.

#: Strong references to in-flight runs. asyncio keeps only weak ones, and a
#: task nobody holds can be garbage-collected mid-plan.
_RUNS: set[asyncio.Task] = set()

#: On a serverless host the instance can be frozen as soon as the response is
#: sent, so there the request stays open until the run finishes. The client
#: polls either way, so it behaves the same; it just doesn't depend on this
#: request surviving.
_RUN_INLINE = bool(os.environ.get("VERCEL"))


async def _input_key(session: AsyncSession, user_id: uuid.UUID, body: ActionRunIn) -> str:
    """Fingerprint of everything the plan depends on besides the market."""
    holdings = await HoldingRepository(session).list_for_user(user_id)
    cash = (await UserSettingsRepository(session).get_or_create(user_id)).invest_cash_cents
    material = {
        "request": body.model_dump(exclude={"force"}),
        "holdings": sorted((h.symbol.upper(), round(float(h.qty), 6)) for h in holdings),
        "cash_cents": int(cash or 0),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _is_stale(run: ActionPlanRun) -> bool:
    started = _aware(run.created_at) or datetime.now(UTC)
    return (datetime.now(UTC) - started).total_seconds() > settings.action_run_stale_seconds


def _run_out(run: ActionPlanRun, *, reused: bool = False) -> ActionRunOut:
    return ActionRunOut(
        id=str(run.id),
        status=run.status,
        period=run.period.isoformat(),
        request=dict(run.request or {}),
        result=ActionPlanOut.model_validate(run.result) if run.result else None,
        error=run.error,
        created_at=(_aware(run.created_at) or datetime.now(UTC)).isoformat(),
        finished_at=_aware(run.finished_at).isoformat() if run.finished_at else None,
        reused=reused,
    )


async def _expire_if_stale(session: AsyncSession, run: ActionPlanRun | None) -> None:
    """A run still "running" long after any plan could take was cut off — the
    server restarted, or a serverless instance was frozen. Say so, so the app
    stops waiting and offers to try again."""
    if run is not None and run.status == "running" and _is_stale(run):
        run.status = "failed"
        run.error = "The plan was interrupted before it finished. Build it again."
        run.finished_at = datetime.now(UTC)
        await session.commit()


async def _execute_run(
    factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    body: ActionPlanIn,
) -> None:
    """Build the plan on a session of its own and record how it went."""
    async with factory() as session:
        status, result, error = "failed", None, None
        try:
            service = PortfolioActionsService(
                session,
                get_market_data_provider(),
                get_advice_llm_provider(),
                MarketContextService(session, get_news_provider(), get_websearch_provider()),
                broker=get_snaptrade_gateway(),
                cipher=get_cipher(),
            )
            plan = await service.plan(user_id, body)
            status, result = "done", plan.model_dump(mode="json")
        except ServiceError as exc:
            error = str(exc) or "Couldn't build a plan."
        except Exception:  # noqa: BLE001 — record it; nobody is waiting on a traceback
            logger.exception("Action plan run %s failed", run_id)
            error = "Something went wrong building the plan. Try again in a minute."
        await session.rollback()
        run = await session.get(ActionPlanRun, run_id)
        if run is None:
            return
        run.status, run.result, run.error = status, result, error
        run.finished_at = datetime.now(UTC)
        await session.commit()


@router.post("/actions/runs", response_model=ActionRunOut)
async def start_action_run(
    body: ActionRunIn, user: CurrentUser, session: SessionDep
) -> ActionRunOut:
    """Start building a plan, or hand back one that already answers this.

    * A plan already running for this user is returned rather than a second
      one started: a double tap must not double the model calls.
    * A finished plan from this trading period, for the same inputs and the
      same holdings, is returned as it is (``reused``) unless ``force``. That
      is what makes asking twice give the same answer. A plan that had to fall
      back to rules is not reused: the next ask should try the model again.
    """
    repo = ActionRunRepository(session)
    running = await repo.running(user.id)
    await _expire_if_stale(session, running)
    if running is not None and running.status == "running":
        return _run_out(running)

    period = market_period()
    key = await _input_key(session, user.id, body)
    if not body.force:
        prior = await repo.reusable(user.id, key, period)
        if prior is not None and prior.result is not None:
            source = prior.result.get("source")
            if source == "ai" or not body.use_ai or not settings.ai_available:
                return _run_out(prior, reused=True)

    plan_in = ActionPlanIn.model_validate(body.model_dump(exclude={"force"}))
    run = await repo.add(
        ActionPlanRun(
            user_id=user.id,
            status="running",
            period=period,
            input_key=key,
            request=plan_in.model_dump(mode="json"),
        )
    )
    await session.commit()

    factory = async_sessionmaker(bind=session.bind, class_=AsyncSession, expire_on_commit=False)
    task = asyncio.create_task(_execute_run(factory, run.id, user.id, plan_in))
    _RUNS.add(task)
    task.add_done_callback(_RUNS.discard)
    if _RUN_INLINE:
        await asyncio.shield(task)
        await session.refresh(run)
    return _run_out(run)


@router.get("/actions/runs/latest", response_model=ActionRunOut | None)
async def latest_action_run(user: CurrentUser, session: SessionDep) -> ActionRunOut | None:
    run = await ActionRunRepository(session).latest(user.id)
    await _expire_if_stale(session, run)
    return _run_out(run) if run is not None else None


@router.get("/actions/runs/{run_id}", response_model=ActionRunOut)
async def get_action_run(run_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> ActionRunOut:
    run = await ActionRunRepository(session).get(user.id, run_id)
    if run is None:
        raise NotFound("No such plan.")
    await _expire_if_stale(session, run)
    return _run_out(run)


@router.post("/actions/accept", response_model=ProposalOut)
async def accept_actions(
    body: ActionAcceptIn, user: CurrentUser, service: PortfolioActionsServiceDep
) -> ProposalOut:
    """Write the plan to the trade checklist as PROPOSED orders. Each still
    needs its own approval before anything reaches a broker."""
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
