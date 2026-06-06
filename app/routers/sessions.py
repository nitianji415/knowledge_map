"""学习会话路由。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import AppUser
from app.routers.deps import get_service
from app.schemas import (
    BackgroundFollowupIn,
    BackgroundFollowupOut,
    BackgroundQuestionIn,
    BackgroundQuestionOut,
    CreateSessionIn,
    CreateSessionOut,
    MessageOut,
    MessagesOut,
    NodeOut,
    NodesOut,
    PreviewTopicsIn,
    PreviewTopicsOut,
    SessionsOut,
)
from app.services.auth import get_current_user
from app.services.knowledge import KnowledgeMapService

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("/background-questions", response_model=BackgroundQuestionOut)
async def create_background_questions(
    payload: BackgroundQuestionIn,
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> BackgroundQuestionOut:
    questions = await service.background_questions(payload.model_dump())
    return BackgroundQuestionOut.model_validate({"questions": questions})


@router.post("/background-followup", response_model=BackgroundFollowupOut)
async def create_background_followup(
    payload: BackgroundFollowupIn,
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> BackgroundFollowupOut:
    """用户答完一轮诊断题后,问 AI 还要不要继续追问。"""
    result = await service.background_followup(payload.model_dump())
    return BackgroundFollowupOut.model_validate(result)


@router.post("/preview-topics", response_model=PreviewTopicsOut)
async def preview_topics(
    payload: PreviewTopicsIn,
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> PreviewTopicsOut:
    """预览-确认流程的预览阶段:轻量 LLM 调用,只返回主干 title + summary 列表。

    失败时返回 502,前端按"出错重试"提示(用户选 Q3 的"报错重试"路径)。
    """
    try:
        topics = await service.preview_topics(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"预览失败,请重试:{exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"预览失败,请重试:{exc}",
        ) from exc
    return PreviewTopicsOut.model_validate({"topics": topics})


@router.post("/{session_id}/grow-children")
async def grow_children(
    session_id: str,
    mode: str = "Lite",
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> StreamingResponse:
    """SSE:对 session 内还没有 children 的 level-1 节点,流式生成 children。

    每完成一支推一条 `event: branch_done\\ndata: {...}`,全部完成推 `event: all_done`。
    用于预览-确认流程后的"一支支长出"动画。
    """
    async def event_stream():
        try:
            async for event_type, data in service.grow_children_stream(db, session_id, mode=mode):
                payload = json.dumps(data, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {payload}\n\n"
        except LookupError as exc:
            err_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {err_payload}\n\n"
        except Exception as exc:  # noqa: BLE001
            err_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {err_payload}\n\n"

    headers = {"Cache-Control": "no-cache", "Connection": "close", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers=headers,
    )


@router.post("/{session_id}/nodes/{node_id}/first-principles")
async def first_principles(
    session_id: str,
    node_id: str,
    request: Request,
    max_depth: int = 6,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> StreamingResponse:
    """SSE:第一性原理"拆到底"。从 node_id 起点逐层往下拆出更底层的前置依赖。

    每拆完一层推一条 `event: fp_layer`,全部完成推 `event: fp_done`。
    客户端断开连接(点停止/关页)→ 后端检测到 is_disconnected 即收手,不再烧 token。
    """
    # max_depth 兜底夹紧,防止前端传入异常值导致失控
    safe_max_depth = max(1, min(int(max_depth), 10))

    async def event_stream():
        try:
            async for event_type, data in service.first_principles_stream(
                db,
                session_id,
                node_id,
                max_depth=safe_max_depth,
                is_disconnected=request.is_disconnected,
            ):
                payload = json.dumps(data, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {payload}\n\n"
        except LookupError as exc:
            err_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {err_payload}\n\n"
        except Exception as exc:  # noqa: BLE001
            err_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {err_payload}\n\n"

    headers = {"Cache-Control": "no-cache", "Connection": "close", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers=headers,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateSessionOut)
async def create_session(
    payload: CreateSessionIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    user: AppUser = Depends(get_current_user),
) -> CreateSessionOut:
    result = await service.create_session(db, payload.model_dump(), user_id=user.id)
    return CreateSessionOut(
        session_id=result["session_id"],
        current_node_id=result["current_node_id"],
        initial_nodes=[NodeOut.model_validate(n) for n in result["initial_nodes"]],
        messages=[MessageOut.model_validate(m) for m in result["messages"]],
    )


@router.get("", response_model=SessionsOut)
async def list_sessions(
    search: str = "",
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    user: AppUser = Depends(get_current_user),
) -> SessionsOut:
    rows = await service.list_sessions(db, search, user_id=user.id)
    return SessionsOut.model_validate({"sessions": rows})


@router.get("/{session_id}/tree", response_model=NodesOut)
async def get_tree(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> NodesOut:
    nodes = await service.get_tree(db, session_id)
    return NodesOut(nodes=[NodeOut.model_validate(n) for n in nodes])


@router.get("/{session_id}/messages", response_model=MessagesOut)
async def get_messages(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> MessagesOut:
    messages = await service.get_messages(db, session_id)
    return MessagesOut(messages=[MessageOut.model_validate(m) for m in messages])
