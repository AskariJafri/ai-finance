"""AI Committee service — gathers real market data for a ticker, runs the
multi-agent committee in a single LLM call, and persists each session.

The LLM only interprets numbers we computed; if it's disabled or unreachable
the verdict degrades to a transparent rules-based read (never a fabricated one).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.committee import rules_only, run_committee
from app.ai.llm.base import LLMProvider
from app.ai.llm.budget import llm_budget
from app.analytics.indicators import compute_indicators
from app.core.config import settings
from app.integrations.marketdata.provider import MarketDataError, MarketDataProvider
from app.repositories.committee import AnalysisSessionRepository
from app.schemas.committee import AgentTakeOut, CommitteeResult, SessionSummary
from app.services.autotrader_service import rules_verdict
from app.services.errors import NotFound, ValidationFailed
from app.services.market_context_service import MarketContextService
from app.services.market_data_service import MarketDataService


class CommitteeService:
    def __init__(
        self,
        session: AsyncSession,
        provider: MarketDataProvider,
        llm: LLMProvider,
        context: MarketContextService | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.llm = llm
        self.market_data = MarketDataService(session, provider)
        self.repo = AnalysisSessionRepository(session)
        # Optional keyword so every existing construction keeps working.
        self.context = context

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

        if settings.ai_available and await self.llm.is_available():
            # One ticker, user-initiated: the cheapest place research can run,
            # and the one where a headline is most likely to matter.
            market = await self.context.context([sym]) if self.context is not None else None
            with llm_budget(
                wait=settings.llm_wait_analyze_seconds,
                max_pass_sleep=settings.llm_max_pass_sleep_seconds,
                max_output_tokens=settings.llm_committee_max_tokens,
            ):
                result = await run_committee(
                    sym,
                    quote_d,
                    ind,
                    self.llm,
                    rules=rules_verdict(ind, held=False, symbol=sym),
                    market=market,
                )
        else:
            # Offline stays free and transparent — no news fetched, none needed.
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
            # Sessions recorded before the research layer existed have neither
            # key; the defaults are what they always effectively meant.
            news_weight=p.get("news_weight", "absent"),
            context=p.get("context"),
            created_at=row.created_at.isoformat(),
        )
