"""运行时应用配置 router(admin 专用)。

GET   /api/settings          列出 SETTING_KEYS 白名单里所有 key 的当前值 + 来源
PATCH /api/settings          批量更新 {key: 新值};空值删除 DB 覆盖
POST  /api/settings/test     不写 DB,试一次 LLM 联通
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import AppUser
from app.schemas import (
    SETTING_KEYS,
    SettingItem,
    SettingsOut,
    TestConnectionIn,
    TestConnectionOut,
    UpdateSettingsIn,
)
from app.schemas.settings import SETTING_GROUPS, SettingGroup
from app.services.auth import require_admin
from app.services.settings_store import get_layered_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
async def list_settings(
    _admin: AppUser = Depends(require_admin),
) -> SettingsOut:
    layered = get_layered_settings()
    items: list[SettingItem] = []
    for key, meta in SETTING_KEYS.items():
        resolved = layered.get(key)
        sensitive = bool(meta.get("sensitive"))
        display_value = layered.mask(resolved.value) if sensitive and resolved.value else resolved.value
        items.append(
            SettingItem(
                key=key,
                label=str(meta.get("label") or key),
                description=str(meta.get("description") or ""),
                group=str(meta.get("group") or "advanced"),
                value=display_value,
                sensitive=sensitive,
                is_set=bool(resolved.value),
                source=resolved.source,  # type: ignore[arg-type]
            )
        )
    groups = [
        SettingGroup(
            key=key,
            title=str(meta.get("title") or key),
            description=str(meta.get("description") or ""),
            order=int(meta.get("order") or 99),
        )
        for key, meta in SETTING_GROUPS.items()
    ]
    groups.sort(key=lambda g: g.order)
    return SettingsOut(items=items, groups=groups)


@router.patch("", response_model=SettingsOut)
async def update_settings(
    payload: UpdateSettingsIn,
    admin: AppUser = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> SettingsOut:
    layered = get_layered_settings()
    for key, value in (payload.updates or {}).items():
        if key not in SETTING_KEYS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"未知配置 key: {key}",
            )
        await layered.upsert(db, key, value or "", admin.id)
    # 必须先 commit 再 reload:reload 用的是独立 session,只 flush 的话读不到本次改动,
    # 会把内存缓存刷成旧值,导致"存了 key 当场不生效、要重启"。
    await db.commit()
    await layered.reload()
    return await list_settings(admin)


@router.post("/test", response_model=TestConnectionOut)
async def test_connection(
    payload: TestConnectionIn,
    _admin: AppUser = Depends(require_admin),
) -> TestConnectionOut:
    """用前端传过来的 (api_key, model, base_url) 试一次最便宜的 chat,不写 DB。

    用来在设置页让用户「保存前先试一下」,不污染真实配置。
    """
    layered = get_layered_settings()
    env = layered.env_settings()
    base_url = (payload.base_url or env.llm_base_url).strip() or "https://api.deepseek.com/v1"
    model = (payload.model or env.llm_model).strip() or "deepseek-chat"

    client = AsyncOpenAI(api_key=payload.api_key, base_url=base_url, timeout=15.0)
    start = time.perf_counter()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=4,
            temperature=0.0,
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        snippet = (response.choices[0].message.content or "").strip()[:40]
        return TestConnectionOut(
            ok=True,
            detail=f"模型回 '{snippet or '(空)'}'",
            latency_ms=elapsed,
        )
    except httpx.HTTPError as exc:
        return TestConnectionOut(ok=False, detail=f"网络错误: {exc!r}"[:240])
    except Exception as exc:  # noqa: BLE001
        return TestConnectionOut(ok=False, detail=f"调用失败: {exc!r}"[:240])
