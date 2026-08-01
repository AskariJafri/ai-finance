"""AI Committee service — gathers real market data for a ticker, runs the
multi-agent committee (local Ollama, free), and persists each session.

The LLM only interprets numbers we computed; if it's disabled or unreachable
the verdict degrades to a transparent rules-based read (never a fabricated one).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.committee import rules_only, run_committee
from app.ai.llm.base import LLMProvider
from app.analytics.indicators import compute_indicators
from app.core.config import settings
from app.integrations.marketdata.provider import MarketDataError, MarketDataProvider
from app.repositories.committee import AnalysisSessionRepository
from app.schemas.committee import AgentTakeOut, CommitteeResult, SessionSummary
from app.services.errors import NotFound, ValidationFailed
from app.services.market_data_service import MarketDataService


class CommitteeService:
    def __init__(
        self, session: AsyncSession, provider: MarketDataProvider, llm: LLMProvider
    ) -> None:
        self.session = session
        self.provider = provider
        self.llm = llm
        self.market_data = MarketDataService(session, provider)
        self.repo = AnalysisSessionRepository(session)

    async def analyze(self, user_id: uuid.UUID, symbol: str) -> CommitteeResult:
        sym = symbol.strip().upper()
        try:
            quote = await self.provider.get_quote(sym)
        except MarketDataError as exc:
            raise ValidationFailed(f"Couldn't find a ticker '{sym}'. Check the symbol.") from exc

        quote_d = {
            "price": round(quote.price, 4),
            "currency": quote.currency,
            "name": quote.name,
        }
        await self.market_data.sync_bars([sym])
        series = (await self.market_data.get_close_series([sym])).get(sym, [])
        closes = [c for _, c in series]
        try:
            ind = compute_indicators(closes)
        except ValueError as exc:
            raise ValidationFailed(
                "Not enough price history to analyze this ticker yet — try again shortly."
            ) from exc

        if settings.ai_enabled and await self.llm.is_available():
            result = await run_committee(sym, quote_d, ind, self.llm)
        else:
            result = rules_only(sym, quote_d, ind)

        row = await self.repo.add(
            user_id=user_id,
            symbol=sym,
            action=result["action"],
            confidence=result["confidence"],
            summary=result["summary"],
            source=result["source"],
            payload=result,
        )
        await self.session.commit()
        return self._to_result(row)

    async def list_sessions(self, user_id: uuid.UUID) -> list[SessionSummary]:
        rows = await self.repo.list_for_user(user_id)
        return [
            SessionSummary(
                id=str(r.id),
                symbol=r.symbol,
                action=r.action,
                confidence=r.confidence,
                source=r.source,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ]

    async def get_session(self, user_id: uuid.UUID, session_id: uuid.UUID) -> CommitteeResult:
        row = await self.repo.get(user_id, session_id)
        if row is None:
            raise NotFound("Analysis not found.")
        return self._to_result(row)

    @staticmethod
    def _to_result(row) -> CommitteeResult:
        p = row.payload or {}
        return CommitteeResult(
            id=str(row.id),
            symbol=row.symbol,
            price=p.get("price"),
            currency=p.get("currency", "USD"),
            action=row.action,
            confidence=row.confidence,
            summary=row.summary,
            source=row.source,
            agents=[AgentTakeOut(**a) for a in p.get("agents", [])],
            indicators=p.get("indicators", {}),
            created_at=row.created_at.isoformat(),
        )
