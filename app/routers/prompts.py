"""Prompt 模板路由(admin 专用)。

GET   /api/prompts            列出所有可编辑 prompt + 当前值 + 默认值 + 是否被覆盖
PATCH /api/prompts            批量更新 {key: 新模板};空值删除 DB 覆盖
POST  /api/prompts/{key}/reset 单独重置一条到默认
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import AppUser
from app.schemas import (
    PromptItem,
    PromptsOut,
    ResetPromptOut,
    UpdatePromptsIn,
)
from app.services.auth import require_admin
from app.services.prompt_defaults import DEFAULT_PROMPTS
from app.services.prompt_store import get_prompt_store

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


@router.get("", response_model=PromptsOut)
async def list_prompts(
    _admin: AppUser = Depends(require_admin),
) -> PromptsOut:
    store = get_prompt_store()
    items = [PromptItem.model_validate(item) for item in store.list_with_metadata()]
    return PromptsOut(items=items)


@router.patch("", response_model=PromptsOut)
async def update_prompts(
    payload: UpdatePromptsIn,
    admin: AppUser = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> PromptsOut:
    store = get_prompt_store()
    for key, value in (payload.updates or {}).items():
        if key not in DEFAULT_PROMPTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"未登记的 prompt key: {key}",
            )
        await store.upsert(db, key, value or "", admin.id)
    # 先 commit 再 reload:reload 走独立 session,只 flush 读不到本次改动(否则改了 prompt 不生效)
    await db.commit()
    await store.reload()
    return await list_prompts(admin)


@router.post("/{key}/reset", response_model=ResetPromptOut)
async def reset_prompt(
    key: str,
    admin: AppUser = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> ResetPromptOut:
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未登记的 prompt key: {key}",
        )
    store = get_prompt_store()
    await store.upsert(db, key, "", admin.id)
    await db.commit()
    await store.reload()
    return ResetPromptOut(ok=True, key=key)
