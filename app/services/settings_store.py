"""运行时应用配置存储:DB 优先,env 兜底,Fernet 加密敏感字段。

读路径(LayeredSettings.get(key)):
  1. 进程内 cache (lifespan 启动时一次性 load) ── 命中直接返回
  2. cache 缺失 → 抛 KeyError,调用方自己回 env

写路径(update_settings):
  1. PATCH 请求传 {key: 新明文}
  2. 敏感字段先 Fernet 加密再写 DB
  3. 写完调 reload(),刷新进程内 cache

这样 ai.py 这种热路径不用每次都查 DB,但配置改了下次请求就能看到。
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.base import get_session_factory
from app.db.models import AppSetting, now_utc
from app.schemas.settings import SETTING_KEYS


def _fernet_from_secret(secret: str) -> Fernet:
    """把任意长度的 settings_secret 派生成 Fernet 要求的 32-byte url-safe key。"""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


@dataclass
class ResolvedValue:
    """get() 的返回:既知道值,也知道来源(给 UI 显示用)。"""

    value: str
    source: str  # 'db' | 'env' | 'default'


class LayeredSettings:
    """DB → env → Settings default 三层兜底的运行时配置。

    单例,在 main.py lifespan 里 init,所有依赖配置的服务从这里读。
    """

    def __init__(self, env_settings: Settings):
        self._env = env_settings
        self._fernet = _fernet_from_secret(env_settings.settings_secret)
        self._db_cache: dict[str, str] = {}  # key -> 解密后的明文
        self._loaded = False

    async def reload(self) -> None:
        """从 DB 把所有 app_settings 行加载到内存。重复调用安全。"""
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(select(AppSetting))).scalars().all()
        new_cache: dict[str, str] = {}
        for row in rows:
            raw = row.value or ""
            if row.encrypted and raw:
                try:
                    raw = self._fernet.decrypt(raw.encode("utf-8")).decode("utf-8")
                except InvalidToken:
                    # SETTINGS_SECRET 被改过导致解不出来 —— 当作空,让调用方走 env
                    print(f"[settings_store] decrypt 失败 (key={row.key}),SETTINGS_SECRET 可能被改过")
                    raw = ""
            new_cache[row.key] = raw
        self._db_cache = new_cache
        self._loaded = True

    def get(self, key: str) -> ResolvedValue:
        """优先 DB,其次 env,最后空字符串。"""
        # DB 层
        db_val = self._db_cache.get(key)
        if db_val:  # 空字符串视为未设置,走 env
            return ResolvedValue(value=db_val, source="db")

        # env 层 —— pydantic-settings 已经把它放进 Settings 字段了
        env_val = self._env_value(key)
        if env_val:
            return ResolvedValue(value=str(env_val), source="env")

        # 默认
        return ResolvedValue(value="", source="default")

    def _env_value(self, key: str) -> Any:
        """key 是 SETTINGS_UPPERCASE 风格,映射到 Settings 字段名(小写)。"""
        attr = key.lower()
        return getattr(self._env, attr, None)

    async def upsert(self, db: AsyncSession, key: str, plain_value: str, updated_by: str | None) -> AppSetting:
        """写一条配置。空字符串视为"删除 DB 层覆盖,回退到 env"。"""
        meta = SETTING_KEYS.get(key)
        if meta is None:
            raise ValueError(f"未登记的配置 key: {key}")
        sensitive = bool(meta.get("sensitive"))

        existing = await db.get(AppSetting, key)
        if not plain_value:
            # 删除覆盖
            if existing:
                await db.delete(existing)
            return existing  # type: ignore[return-value]

        stored = (
            self._fernet.encrypt(plain_value.encode("utf-8")).decode("utf-8")
            if sensitive
            else plain_value
        )
        if existing:
            existing.value = stored
            existing.encrypted = sensitive
            existing.updated_at = now_utc()
            existing.updated_by = updated_by
            return existing
        new_row = AppSetting(
            key=key,
            value=stored,
            encrypted=sensitive,
            updated_by=updated_by,
        )
        db.add(new_row)
        return new_row

    def mask(self, value: str) -> str:
        """敏感字段对外展示:'***' + 末 4 位。空字符串不 mask。"""
        if not value:
            return ""
        if len(value) <= 6:
            return "***"
        return "***" + value[-4:]

    def env_settings(self) -> Settings:
        return self._env


# 进程级单例。main.py lifespan 负责 init。
_layered: LayeredSettings | None = None


def init_layered_settings(env_settings: Settings | None = None) -> LayeredSettings:
    global _layered
    _layered = LayeredSettings(env_settings or get_settings())
    return _layered


def get_layered_settings() -> LayeredSettings:
    if _layered is None:
        # lifespan 没跑(例如 import-time 调用) → 临时建一个,不加载 DB
        return init_layered_settings()
    return _layered
