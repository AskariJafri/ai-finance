"""Conversational finance assistant.

Answers two kinds of question in one thread: "how am I doing?" (grounded in the
user's own SQL-computed figures via ``FinanceContextBuilder``) and "what does
this term mean?" (anchored to the reviewed copy in ``glossary``).

Degradation is deliberate and layered, because the LLM here is a local Ollama
model that may simply not be running:

    model answers  ->  reviewed explainer verbatim  ->  honest "AI is offline"

A chat turn is never allowed to 500 because inference failed; the user's own
message is always persisted so the thread stays coherent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import LLMProvider
from app.ai.prompts import CHAT_SYSTEM, build_chat_prompt
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ChatMessage, ChatSession
from app.integrations.marketdata.provider import MarketDataProvider
from app.repositories.chat import ChatRepository
from app.schemas.chat import ChatSuggestion
from app.services import glossary
from app.services.finance_context import FinanceContextBuilder

logger = get_logger(__name__)

DISCLAIMER = (
    "General information generated from your own data — not advice from a "
    "licensed financial professional. This assistant never places trades."
)

_OFFLINE = (
    "The AI assistant isn't reachable right now, so I can't answer that one. "
    "Your figures are still up to date on the Dashboard, Insights and Invest "
    "tabs — those never depend on the AI. If you're running the local model, "
    "check that Ollama is up and AI_ENABLED is on."
)

# Sent with every reply's prompt; older turns are dropped rather than
# summarized (summarizing would need a second slow local inference call).
_HISTORY_TURNS = 6


class ChatService:
    def __init__(
        self,
        session: AsyncSession,
        llm: LLMProvider,
        market_data: MarketDataProvider,
    ) -> None:
        self.session = session
        self.llm = llm
        self.repo = ChatRepository(session)
        self.context = FinanceContextBuilder(session, market_data)

    # --- threads ---

    async def list_sessions(self, user_id: uuid.UUID) -> list[tuple[ChatSession, int]]:
        return await self.repo.list_sessions(user_id)

    async def create_session(self, user_id: uuid.UUID, title: str | None = None) -> ChatSession:
        row = await self.repo.create_session(user_id, title or "New chat")
        await self.session.commit()
        return row

    async def delete_session(self, user_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        deleted = await self.repo.delete_session(user_id, session_id)
        await self.session.commit()
        return deleted

    async def messages(self, user_id: uuid.UUID, session_id: uuid.UUID) -> list[ChatMessage] | None:
        thread = await self.repo.get_session(user_id, session_id)
        if thread is None:
            return None
        return await self.repo.list_messages(session_id)

    # --- the turn ---

    async def send(
        self,
        user_id: uuid.UUID,
        *,
        message: str,
        session_id: uuid.UUID | None,
        include_finances: bool = True,
    ) -> tuple[ChatSession, ChatMessage, ChatMessage] | None:
        """Returns (thread, user message, reply), or None if ``session_id``
        does not belong to this user."""
        question = message.strip()

        if session_id is None:
            thread = await self.repo.create_session(user_id, _title_from(question))
        else:
            found = await self.repo.get_session(user_id, session_id)
            if found is None:
                return None
            thread = found

        # History must be read before the new message is added, otherwise the
        # question would appear twice in the prompt.
        history = [
            (m.role, m.content) for m in await self.repo.recent_turns(thread.id, _HISTORY_TURNS)
        ]
        user_msg = await self.repo.add_message(session_id=thread.id, role="user", content=question)

        terms = glossary.match_terms(question)
        used: list[str] = [f"glossary:{t}" for t in terms]

        finance_text = None
        if include_finances:
            ctx = await self.context.build(user_id)
            if not ctx.is_empty:
                finance_text = ctx.text
                used += ctx.sections

        content, source, model = await self._answer(
            question=question,
            finance_text=finance_text,
            terms=terms,
            history=history,
        )

        reply = await self.repo.add_message(
            session_id=thread.id,
            role="assistant",
            content=content,
            source=source,
            model=model,
            context_used=used if source == "ai" else [f"glossary:{t}" for t in terms],
        )
        # Touch the thread so the history list orders by real activity.
        # Assigning updated_at explicitly: ``onupdate`` only fires when some
        # column is dirty, and a reply changes rows in chat_messages only.
        thread.updated_at = datetime.now(UTC)
        await self.session.commit()
        return thread, user_msg, reply

    async def _answer(
        self,
        *,
        question: str,
        finance_text: str | None,
        terms: list[str],
        history: list[tuple[str, str]],
    ) -> tuple[str, str, str | None]:
        if settings.ai_enabled:
            try:
                content = await self.llm.complete(
                    build_chat_prompt(
                        question=question,
                        finance_context=finance_text,
                        reference=glossary.render_reference(terms) if terms else None,
                        history=history,
                    ),
                    system=CHAT_SYSTEM,
                    # Warmer than advice: this is conversation, not a report.
                    temperature=0.5,
                )
                if content:
                    return content, "ai", getattr(self.llm, "model", None)
            except Exception as exc:  # degrade, never fail the request
                logger.warning("Chat generation failed, degrading: %s", exc)

        if terms:
            return glossary.render_answer(terms), "glossary", None
        return _OFFLINE, "fallback", None

    # --- starters ---

    async def suggestions(self, user_id: uuid.UUID) -> list[ChatSuggestion]:
        """Starter prompts, narrowed to what this user actually has data for."""
        ctx = await self.context.build(user_id)
        items: list[ChatSuggestion] = []

        if "cashflow" in ctx.sections:
            items.append(
                ChatSuggestion(
                    label="Where did my money go?",
                    prompt="Where did my money go over the last 30 days, and what stands out?",
                    group="money",
                )
            )
        if "budget" in ctx.sections:
            items.append(
                ChatSuggestion(
                    label="Am I on track?",
                    prompt="How am I tracking against the 50/30/20 rule this month, "
                    "and what should I change?",
                    group="money",
                )
            )
        if "portfolio" in ctx.sections:
            items += [
                ChatSuggestion(
                    label="Explain my portfolio risk",
                    prompt="Explain my portfolio's risk in plain language. How risky is it really?",
                    group="invest",
                ),
                ChatSuggestion(
                    label="Am I diversified?",
                    prompt="Is my portfolio well diversified, or too concentrated?",
                    group="invest",
                ),
            ]
        else:
            items.append(
                ChatSuggestion(
                    label="How do I start investing?",
                    prompt="I'm in Canada and new to investing. How should I think "
                    "about getting started?",
                    group="invest",
                )
            )

        items += [
            ChatSuggestion(
                label="What is a Sharpe ratio?",
                prompt="What is the Sharpe ratio, and what counts as a good one?",
                group="learn",
            ),
            ChatSuggestion(
                label="Market vs limit orders",
                prompt="What's the difference between a market order and a limit "
                "order, and when should I use each?",
                group="learn",
            ),
            ChatSuggestion(
                label="TFSA vs RRSP",
                prompt="What's the difference between a TFSA and an RRSP, and how "
                "do I decide which to use?",
                group="learn",
            ),
        ]
        return items


def _title_from(question: str) -> str:
    """First line of the question, trimmed — no LLM call just to name a thread."""
    text = " ".join(question.split())
    return (text[:57] + "...") if len(text) > 60 else (text or "New chat")
