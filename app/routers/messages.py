"""消息流式接口 + 划词高亮 PATCH。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sse import format_event
from app.db.base import get_session
from app.db.models import AppUser
from app.routers.deps import get_service
from app.schemas import (
    CreatePeekFollowupIn,
    CreatePeekIn,
    DeepReanswerIn,
    MessageOut,
    NodeOut,
    SendMessageIn,
    UpdateHighlightsIn,
)
from app.services.auth import get_current_user
from app.services.knowledge import KnowledgeMapService

router = APIRouter(prefix="/api/sessions", tags=["messages"])
messages_router = APIRouter(prefix="/api/messages", tags=["messages"])


def _serialize_patch(patch: dict) -> dict:
    return {
        "current_node_id": patch["current_node_id"],
        "updated_node_id": patch["updated_node_id"],
        "created_node_ids": patch["created_node_ids"],
        "status": patch["status"],
        "nodes": [NodeOut.model_validate(n).model_dump(mode="json") for n in patch["nodes"]],
        "messages": [MessageOut.model_validate(m).model_dump(mode="json") for m in patch["messages"]],
    }


@router.post("/{session_id}/messages/stream")
async def send_message_stream(
    session_id: str,
    payload: SendMessageIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> StreamingResponse:
    async def event_stream():
        # 真流式:explain 逐字推 token;done 事件带与原来同构的 patch
        async for kind, data in service.receive_message_stream(db, session_id, payload.model_dump()):
            if kind == "token":
                yield format_event("token", {"text": data})
            elif kind == "done":
                yield format_event("done", _serialize_patch(data))

    headers = {"Cache-Control": "no-cache", "Connection": "close", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers=headers,
    )


@messages_router.patch("/{message_id}/highlights", response_model=MessageOut)
async def update_message_highlights(
    message_id: str,
    payload: UpdateHighlightsIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> MessageOut:
    try:
        message = await service.update_message_highlights(
            db, message_id, [h.model_dump() for h in payload.highlights]
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MessageOut.model_validate(message)


@messages_router.post("/{message_id}/peeks", response_model=MessageOut)
async def create_message_peek(
    message_id: str,
    payload: CreatePeekIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> MessageOut:
    try:
        message = await service.create_message_peek(db, message_id, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MessageOut.model_validate(message)


@messages_router.post("/{message_id}/peeks/{peek_id}/followups", response_model=MessageOut)
async def create_peek_followup(
    message_id: str,
    peek_id: str,
    payload: CreatePeekFollowupIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> MessageOut:
    try:
        message = await service.add_peek_followup(db, message_id, peek_id, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MessageOut.model_validate(message)


@messages_router.post("/{message_id}/deep-search", response_model=MessageOut)
async def run_deep_search(
    message_id: str,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> MessageOut:
    try:
        message = await service.deep_search_message(db, message_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MessageOut.model_validate(message)


@messages_router.post("/{message_id}/deep-reanswer", response_model=MessageOut)
async def reanswer_with_deep_search(
    message_id: str,
    payload: DeepReanswerIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> MessageOut:
    try:
        message = await service.reanswer_with_deep_search(db, message_id, payload.mode)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MessageOut.model_validate(message)
