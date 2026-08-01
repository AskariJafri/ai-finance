from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import ChatMessage, ChatSession

MessageRole = Literal["user", "assistant"]
# "ai": the model answered. "glossary": the model was unavailable and a
# hand-written explainer matched the question. "fallback": neither.
AnswerSource = Literal["ai", "glossary", "fallback"]


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: MessageRole
    content: str
    source: AnswerSource | None = None
    model: str | None = None
    context_used: list[str] = Field(default_factory=list)
    created_at: str

    @classmethod
    def from_model(cls, m: ChatMessage) -> ChatMessageOut:
        return cls(
            id=m.id,
            session_id=m.session_id,
            role=m.role,  # type: ignore[arg-type]
            content=m.content,
            source=m.source,  # type: ignore[arg-type]
            model=m.model,
            context_used=list(m.context_used or []),
            created_at=m.created_at.isoformat(),
        )


class ChatSessionOut(BaseModel):
    id: uuid.UUID
    title: str
    message_count: int = 0
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, s: ChatSession, message_count: int = 0) -> ChatSessionOut:
        return cls(
            id=s.id,
            title=s.title,
            message_count=message_count,
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
        )


class ChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: uuid.UUID | None = None
    # Off lets the user ask a purely educational question ("what is a covered
    # call?") without their balances being put in the prompt.
    include_finances: bool = True


class ChatSendResponse(BaseModel):
    session_id: uuid.UUID
    user_message: ChatMessageOut
    reply: ChatMessageOut
    disclaimer: str


class ChatSuggestion(BaseModel):
    label: str
    prompt: str
    group: Literal["money", "invest", "learn"]


class ChatStatus(BaseModel):
    """What the client should expect before the user types anything."""

    ai_enabled: bool
    llm_available: bool
    model: str | None = None
    suggestions: list[ChatSuggestion]
    disclaimer: str
