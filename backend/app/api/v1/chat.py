from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.ai.factory import get_chat_llm_provider
from app.api.deps import ChatServiceDep, CurrentUser
from app.core.config import settings
from app.schemas.chat import (
    ChatMessageOut,
    ChatSendRequest,
    ChatSendResponse,
    ChatSessionOut,
    ChatStatus,
    LLMEndpointStatus,
)
from app.services.chat_service import DISCLAIMER

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/status", response_model=ChatStatus)
async def chat_status(user: CurrentUser, service: ChatServiceDep) -> ChatStatus:
    """Whether the assistant can answer, plus starter prompts for this user."""
    llm = get_chat_llm_provider()
    available = await llm.is_available() if settings.ai_available else False
    # What the pool has learned from real calls. Labels only — the keys never
    # leave the server.
    pool_status = getattr(llm, "status", None)
    endpoints = [LLMEndpointStatus(**m) for m in pool_status()] if callable(pool_status) else []
    return ChatStatus(
        ai_enabled=settings.ai_available,
        llm_available=available,
        model=getattr(llm, "model", None) if available else None,
        endpoints=endpoints,
        suggestions=await service.suggestions(user.id),
        disclaimer=DISCLAIMER,
    )


@router.get("/sessions", response_model=list[ChatSessionOut])
async def list_sessions(user: CurrentUser, service: ChatServiceDep) -> list[ChatSessionOut]:
    return [ChatSessionOut.from_model(s, n) for s, n in await service.list_sessions(user.id)]


@router.post("/sessions", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(user: CurrentUser, service: ChatServiceDep) -> ChatSessionOut:
    return ChatSessionOut.from_model(await service.create_session(user.id))


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: uuid.UUID, user: CurrentUser, service: ChatServiceDep) -> None:
    if not await service.delete_session(user.id, session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
async def list_messages(
    session_id: uuid.UUID, user: CurrentUser, service: ChatServiceDep
) -> list[ChatMessageOut]:
    rows = await service.messages(user.id, session_id)
    if rows is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    return [ChatMessageOut.from_model(m) for m in rows]


@router.post("/messages", response_model=ChatSendResponse)
async def send_message(
    body: ChatSendRequest, user: CurrentUser, service: ChatServiceDep
) -> ChatSendResponse:
    """Ask a question; a hosted model generates the reply.

    Never fails because inference failed: the reply degrades to the app's own
    reviewed explainers, and its ``source`` field says which path was taken.
    """
    result = await service.send(
        user.id,
        message=body.message,
        session_id=body.session_id,
        include_finances=body.include_finances,
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    thread, user_msg, reply = result
    return ChatSendResponse(
        session_id=thread.id,
        user_message=ChatMessageOut.from_model(user_msg),
        reply=ChatMessageOut.from_model(reply),
        disclaimer=DISCLAIMER,
    )
