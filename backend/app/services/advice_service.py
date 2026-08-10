"""Monthly financial advice: SQL figures first, LLM narration second.

Follows the dashboard guardrail — the LLM never produces numbers, it only
explains figures computed by ``ReportsService``. Successful AI output is
cached per (user, month) because local generation is slow; the rule-based
fallback is returned live and never cached so the AI is retried next time.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.ai.prompts import ADVICE_SYSTEM, build_advice_prompt
from app.analytics.reports import ReportsService
from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.ai_insights import AIInsightRepository
from app.repositories.user_settings import UserSettingsRepository
from app.schemas.advice import AdviceOut

logger = get_logger(__name__)

KIND_MONTHLY_ADVICE = "monthly_advice"

DISCLAIMER = (
    "Automated general information generated from your transaction data. "
    "Not financial advice from a licensed professional; consider consulting "
    "one for decisions."
)


class AdviceService:
    def __init__(self, session: AsyncSession, llm: LLMProvider) -> None:
        self.session = session
        self.llm = llm
        self.insights = AIInsightRepository(session)
        self.user_settings = UserSettingsRepository(session)
        self.reports = ReportsService(session)

    async def get(self, user_id: uuid.UUID, month: str) -> AdviceOut:
        """Cached advice only — never calls the LLM."""
        cached = await self.insights.get(user_id, KIND_MONTHLY_ADVICE, month)
        if cached is not None:
            return AdviceOut(
                month=month,
                content=cached.content,
                source="ai",
                model=cached.model,
                generated_at=cached.updated_at.isoformat(),
                disclaimer=DISCLAIMER,
            )
        return AdviceOut(month=month, source="none", disclaimer=DISCLAIMER)

    async def generate(self, user_id: uuid.UUID, month: str, *, force: bool = False) -> AdviceOut:
        if not force:
            cached = await self.get(user_id, month)
            if cached.source == "ai":
                return cached

        row = await self.user_settings.get_for_user(user_id)
        override = row.monthly_income_override_cents if row is not None else None
        figures = await self.reports.figures_for_advice(
            user_id, month, income_override_cents=override
        )

        if settings.ai_available:
            try:
                content = await self.llm.complete(
                    build_advice_prompt(figures),
                    system=ADVICE_SYSTEM,
                    temperature=0.4,
                )
                if content:
                    saved = await self.insights.upsert(
                        user_id=user_id,
                        kind=KIND_MONTHLY_ADVICE,
                        period=month,
                        content=content,
                        model=getattr(self.llm, "model", None),
                    )
                    await self.session.commit()
                    return AdviceOut(
                        month=month,
                        content=content,
                        source="ai",
                        model=saved.model,
                        generated_at=saved.updated_at.isoformat(),
                        disclaimer=DISCLAIMER,
                    )
            except Exception as exc:  # degrade like AICategorizer: never fail the request
                logger.warning("Advice generation failed, using fallback: %s", exc)

        return AdviceOut(
            month=month,
            content=self._fallback_tips(figures),
            source="fallback",
            disclaimer=DISCLAIMER,
        )

    @staticmethod
    def _fallback_tips(figures: dict) -> str:
        """Deterministic tips from the computed figures (no LLM)."""
        tips: list[str] = []
        income = figures["income"]
        savings, wants = figures["savings"], figures["wants"]

        if income <= 0:
            tips.append(
                "No income was detected this month, so the 50/30/20 split "
                "can't be computed. If that's wrong, set your monthly income "
                "manually in the budget settings."
            )
        else:
            if savings["share"] >= 0.2:
                tips.append(
                    f"You saved {savings['share']:.0%} of your income this month — "
                    "at or above the 20% target. Consider moving the surplus into "
                    "a TFSA or high-interest savings account."
                )
            elif savings["amount"] < 0:
                tips.append(
                    f"You spent ${-savings['amount']:,.2f} more than you earned "
                    "this month. Review the biggest categories below for cuts."
                )
            else:
                tips.append(
                    f"You saved {savings['share']:.0%} of income — below the 20% "
                    "target. Trimming discretionary spending could close the gap."
                )
            if wants["share"] > 0.3:
                tips.append(
                    f"Wants took {wants['share']:.0%} of income (target 30%). "
                    "That's the easiest place to cut back."
                )

        if figures["top_categories"]:
            top = figures["top_categories"][0]
            tips.append(
                f"Your largest spending category was {top['category']} at ${top['amount']:,.2f}."
            )
        if figures["uncategorized_count"]:
            tips.append(
                f"{figures['uncategorized_count']} transactions are uncategorized — "
                "categorizing them makes this breakdown more accurate."
            )
        return "\n\n".join(f"• {t}" for t in tips)
