"""划词联网搜索:用户在主对话或速览卡里选一段文本,
点【联网搜索】触发后端立即跑一次网页搜索,把结果返回给前端显示在 popover 里。
不关联任何 message —— 临时检索,不写库。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.db.base import get_session  # noqa: F401  保留以备扩展
from app.db.models import AppUser
from app.routers.deps import get_service
from app.schemas import WebSearchIn, WebSearchOut
from app.services.auth import get_current_user
from app.services.knowledge import KnowledgeMapService

router = APIRouter(prefix="/api/web-search", tags=["web-search"])


@router.post("", response_model=WebSearchOut)
async def run_ad_hoc_web_search(
    payload: WebSearchIn,
    service: KnowledgeMapService = Depends(get_service),
    _user: AppUser = Depends(get_current_user),
) -> WebSearchOut:
    """用户划词触发的临时联网搜索。"""
    query = payload.query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="搜索关键词不能为空",
        )
    sources = await service.ad_hoc_web_search(query, limit=payload.limit)
    return WebSearchOut(query=query, sources=sources)
