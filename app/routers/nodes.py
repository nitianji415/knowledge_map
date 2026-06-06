"""节点更新路由 + 节点维度上的拆分浮层接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import AppUser
from app.routers.deps import get_service
from app.schemas import (
    CautionNoteIn,
    CautionNoteOut,
    MessageOut,
    MultiAngleSubdivideIn,
    MultiAngleSubdivideOut,
    NodeOut,
    NodeSearchIn,
    NodeSearchOut,
    SubdivisionOptionsIn,
    SubdivisionOptionsOut,
    UpdateNodeIn,
    UpdateNodeOut,
)
from app.services.auth import get_current_user
from app.services.knowledge import KnowledgeMapService

router = APIRouter(prefix="/api/nodes", tags=["nodes"])
session_node_router = APIRouter(prefix="/api/sessions", tags=["nodes"])


@router.patch("/{node_id}", response_model=UpdateNodeOut)
async def update_node(
    node_id: str,
    payload: UpdateNodeIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> UpdateNodeOut:
    body = payload.model_dump(exclude_unset=True)
    try:
        node, nodes = await service.update_node(db, node_id, body)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return UpdateNodeOut(
        node=NodeOut.model_validate(node),
        nodes=[NodeOut.model_validate(n) for n in nodes],
    )


@session_node_router.post(
    "/{session_id}/nodes/{node_id}/subdivision-options",
    response_model=SubdivisionOptionsOut,
)
async def subdivision_options(
    session_id: str,
    node_id: str,
    payload: SubdivisionOptionsIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> SubdivisionOptionsOut:
    try:
        result = await service.suggest_subdivision_options(db, session_id, node_id, payload.mode)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SubdivisionOptionsOut.model_validate(result)


@session_node_router.post(
    "/{session_id}/nodes/{node_id}/caution-note",
    response_model=CautionNoteOut,
)
async def caution_note(
    session_id: str,
    node_id: str,
    payload: CautionNoteIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> CautionNoteOut:
    try:
        message = await service.add_caution_note(
            db, session_id, node_id, payload.rationale, payload.mode
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CautionNoteOut(message=MessageOut.model_validate(message))


@session_node_router.post(
    "/{session_id}/nodes/search",
    response_model=NodeSearchOut,
)
async def search_nodes(
    session_id: str,
    payload: NodeSearchIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> NodeSearchOut:
    """AI 检索当前 session 内的节点。前端只用结果列卡片,不自动跳转。"""
    result = await service.search_nodes(db, session_id, payload.query, limit=payload.limit)
    return NodeSearchOut.model_validate(result)


@session_node_router.post(
    "/{session_id}/nodes/{node_id}/multi-angle-subdivide",
    response_model=MultiAngleSubdivideOut,
)
async def multi_angle_subdivide(
    session_id: str,
    node_id: str,
    payload: MultiAngleSubdivideIn,
    db: AsyncSession = Depends(get_session),
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> MultiAngleSubdivideOut:
    try:
        result = await service.multi_angle_subdivide(
            db,
            session_id,
            node_id,
            payload.mode,
            [a.model_dump() for a in payload.angles],
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MultiAngleSubdivideOut(
        reply=result["reply"],
        current_node_id=result["current_node_id"],
        created_node_ids=result["created_node_ids"],
        nodes=[NodeOut.model_validate(n) for n in result["nodes"]],
        messages=[MessageOut.model_validate(m) for m in result["messages"]],
    )
