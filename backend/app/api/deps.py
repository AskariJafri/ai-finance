"""FastAPI dependency-injection wiring.

Assembles services from repositories, gateways and the AI layer per request.
Keeping construction here (rather than in routers) means routers stay thin and
every collaborator is swappable in tests via dependency overrides.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.factory import (
    get_advice_llm_provider,
    get_ai_categorizer,
    get_chat_llm_provider,
)
from app.analytics.reports import ReportsService
from app.core.crypto import get_cipher
from app.db.models import User
from app.db.session import get_session
from app.integrations.marketdata.factory import get_market_data_provider
from app.integrations.news.factory import get_news_provider
from app.integrations.snaptrade.factory import get_snaptrade_gateway
from app.integrations.websearch.factory import get_websearch_provider
from app.services.advice_service import AdviceService
from app.services.auth_service import AuthService
from app.services.autotrader_service import AutoTraderService
from app.services.brokerage_service import BrokerageService
from app.services.categorization_service import CategorizationService
from app.services.category_service import CategoryService
from app.services.chat_service import ChatService
from app.services.committee_service import CommitteeService
from app.services.dashboard_service import DashboardService
from app.services.deploy_service import DeployService
from app.services.errors import InvalidToken
from app.services.invest_insight_service import InvestInsightService
from app.services.invest_service import InvestService
from app.services.market_context_service import MarketContextService
from app.services.paper_broker_service import PaperBrokerService
from app.services.plaid_service import PlaidService
from app.services.portfolio_actions_service import PortfolioActionsService
from app.services.settings_service import SettingsService
from app.services.transaction_service import TransactionService
from app.services.watchlist_service import WatchlistService

_bearer = HTTPBearer(auto_error=True)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(session)


def get_categorization_service(session: SessionDep) -> CategorizationService:
    return CategorizationService(session, get_ai_categorizer())


def get_category_service(
    session: SessionDep,
    categorizer: Annotated[CategorizationService, Depends(get_categorization_service)],
) -> CategoryService:
    return CategoryService(session, categorizer)


def get_transaction_service(
    session: SessionDep,
    categorizer: Annotated[CategorizationService, Depends(get_categorization_service)],
) -> TransactionService:
    return TransactionService(session, categorizer)


def get_plaid_service(
    session: SessionDep,
    transactions: Annotated[TransactionService, Depends(get_transaction_service)],
) -> PlaidService:
    return PlaidService(session, get_cipher(), transactions)


def get_dashboard_service(session: SessionDep) -> DashboardService:
    return DashboardService(session)


def get_settings_service(session: SessionDep) -> SettingsService:
    return SettingsService(session, get_cipher())


def get_reports_service(session: SessionDep) -> ReportsService:
    return ReportsService(session)


def get_advice_service(session: SessionDep) -> AdviceService:
    return AdviceService(session, get_advice_llm_provider())


def get_invest_service(session: SessionDep) -> InvestService:
    return InvestService(session, get_market_data_provider())


def get_watchlist_service(session: SessionDep) -> WatchlistService:
    return WatchlistService(session, get_market_data_provider())


def get_market_context_service(session: SessionDep) -> MarketContextService:
    """The research layer. Built per request like every other service; the
    providers behind it are cached factories."""
    return MarketContextService(session, get_news_provider(), get_websearch_provider())


def get_committee_service(session: SessionDep) -> CommitteeService:
    return CommitteeService(
        session,
        get_market_data_provider(),
        get_advice_llm_provider(),
        get_market_context_service(session),
    )


def get_deploy_service(session: SessionDep) -> DeployService:
    return DeployService(
        session,
        get_market_data_provider(),
        get_advice_llm_provider(),
        get_market_context_service(session),
    )


def get_portfolio_actions_service(session: SessionDep) -> PortfolioActionsService:
    return PortfolioActionsService(
        session,
        get_market_data_provider(),
        get_advice_llm_provider(),
        get_market_context_service(session),
        broker=get_snaptrade_gateway(),
        cipher=get_cipher(),
    )


def get_paper_broker_service(session: SessionDep) -> PaperBrokerService:
    return PaperBrokerService(session, get_market_data_provider())


def get_autotrader_service(session: SessionDep) -> AutoTraderService:
    return AutoTraderService(
        session, get_market_data_provider(), get_market_context_service(session)
    )


def get_brokerage_service(
    session: SessionDep,
    invest: Annotated[InvestService, Depends(get_invest_service)],
) -> BrokerageService:
    return BrokerageService(session, get_cipher(), get_snaptrade_gateway(), invest)


def get_invest_insight_service(
    session: SessionDep,
    invest: Annotated[InvestService, Depends(get_invest_service)],
) -> InvestInsightService:
    return InvestInsightService(session, get_advice_llm_provider(), invest)


def get_chat_service(session: SessionDep) -> ChatService:
    return ChatService(session, get_chat_llm_provider(), get_market_data_provider())


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> User:
    try:
        return await auth.get_current_user(credentials.credentials)
    except InvalidToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUser = Annotated[User, Depends(get_current_user)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
PlaidServiceDep = Annotated[PlaidService, Depends(get_plaid_service)]
TransactionServiceDep = Annotated[TransactionService, Depends(get_transaction_service)]
CategoryServiceDep = Annotated[CategoryService, Depends(get_category_service)]
DashboardServiceDep = Annotated[DashboardService, Depends(get_dashboard_service)]
SettingsServiceDep = Annotated[SettingsService, Depends(get_settings_service)]
ReportsServiceDep = Annotated[ReportsService, Depends(get_reports_service)]
AdviceServiceDep = Annotated[AdviceService, Depends(get_advice_service)]
InvestServiceDep = Annotated[InvestService, Depends(get_invest_service)]
InvestInsightServiceDep = Annotated[InvestInsightService, Depends(get_invest_insight_service)]
WatchlistServiceDep = Annotated[WatchlistService, Depends(get_watchlist_service)]
CommitteeServiceDep = Annotated[CommitteeService, Depends(get_committee_service)]
DeployServiceDep = Annotated[DeployService, Depends(get_deploy_service)]
PortfolioActionsServiceDep = Annotated[
    PortfolioActionsService, Depends(get_portfolio_actions_service)
]
PaperBrokerServiceDep = Annotated[PaperBrokerService, Depends(get_paper_broker_service)]
AutoTraderServiceDep = Annotated[AutoTraderService, Depends(get_autotrader_service)]
BrokerageServiceDep = Annotated[BrokerageService, Depends(get_brokerage_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
