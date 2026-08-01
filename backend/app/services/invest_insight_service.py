"""AI commentary on invest figures — AdviceService pattern: figures first,
LLM explains, successful output cached per (user, kind, month), deterministic
fallback never cached."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.ai.prompts import INVEST_SYSTEM, build_invest_health_prompt
from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.ai_insights import AIInsightRepository
from app.schemas.advice import AdviceOut
from app.schemas.invest import INVEST_DISCLAIMER
from app.services.invest_service import InvestService

logger = get_logger(__name__)

KIND_HEALTH = "invest_health"


class InvestInsightService:
    def __init__(self, session: AsyncSession, llm: LLMProvider, invest: InvestService) -> None:
        self.session = session
        self.llm = llm
        self.invest = invest
        self.insights = AIInsightRepository(session)

    @staticmethod
    def _period() -> str:
        return date.today().strftime("%Y-%m")

    async def get(self, user_id: uuid.UUID) -> AdviceOut:
        period = self._period()
        cached = await self.insights.get(user_id, KIND_HEALTH, period)
        if cached is not None:
            return AdviceOut(
                month=period,
                content=cached.content,
                source="ai",
                model=cached.model,
                generated_at=cached.updated_at.isoformat(),
                disclaimer=INVEST_DISCLAIMER,
            )
        return AdviceOut(month=period, source="none", disclaimer=INVEST_DISCLAIMER)

    async def generate(self, user_id: uuid.UUID, *, force: bool = False) -> AdviceOut:
        period = self._period()
        if not force:
            cached = await self.get(user_id)
            if cached.source == "ai":
                return cached

        figures = await self.invest.health_figures(user_id)

        if settings.ai_enabled:
            try:
                content = await self.llm.complete(
                    build_invest_health_prompt(figures),
                    system=INVEST_SYSTEM,
                    temperature=0.4,
                )
                if content:
                    saved = await self.insights.upsert(
                        user_id=user_id,
                        kind=KIND_HEALTH,
                        period=period,
                        content=content,
                        model=getattr(self.llm, "model", None),
                    )
                    await self.session.commit()
                    return AdviceOut(
                        month=period,
                        content=content,
                        source="ai",
                        model=saved.model,
                        generated_at=saved.updated_at.isoformat(),
                        disclaimer=INVEST_DISCLAIMER,
                    )
            except Exception as exc:  # degrade, never fail the request
                logger.warning("Invest insight generation failed, fallback: %s", exc)

        return AdviceOut(
            month=period,
            content=self._fallback(figures),
            source="fallback",
            disclaimer=INVEST_DISCLAIMER,
        )

    @staticmethod
    def _fallback(figures: dict) -> str:
        tips: list[str] = []
        bucket = figures.get("risk_bucket")
        if bucket:
            tips.append(
                f"Your portfolio's volatility puts it in the '{bucket}' risk "
                "bucket over the last year."
            )
        conc = figures.get("concentration") or {}
        if conc.get("top_weight") and conc["top_weight"] > 0.25:
            tips.append(
                f"{conc.get('top_symbol')} is {conc['top_weight']:.0%} of your "
                "portfolio — concentration above 25% means one holding drives "
                "your outcomes."
            )
        drifts = figures.get("drifted_positions") or []
        for d in drifts[:3]:
            tips.append(
                f"{d['symbol']} is at {d['weight']:.0%} vs its {d['target']:.0%} "
                "target — consider proposing a rebalance."
            )
        if not figures.get("has_targets"):
            tips.append(
                "Set target weights in the Rebalance screen to unlock drift "
                "monitoring and rebalance proposals."
            )
        if not tips:
            tips.append("No flags this month — portfolio looks consistent with its targets.")
        return "\n\n".join(f"• {t}" for t in tips)
