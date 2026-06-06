"""Prompt 模板存储:DB 优先 / 代码默认值兜底 / {占位符} 安全替换。

和 settings_store.LayeredSettings 一个模式:
  - 启动时 lifespan 调 reload() 把 prompt_templates 全表读到内存
  - 调用方 prompt_store.get(key) 或 .format(key, **vars) 走内存 cache,零额外 DB 调用
  - PATCH /api/prompts 写库后调 reload() 刷新

没用 Fernet:prompt 内容不敏感,不像 LLM key,明文存即可。
"""

from __future__ import annotations

import string
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session_factory
from app.db.models import PromptTemplate, now_utc
from app.services.prompt_defaults import DEFAULT_PROMPTS, PromptMeta


class _SafeFormatter(string.Formatter):
    """{not_a_var} 之类的未知占位符保留原样,不抛 KeyError。

    保证 admin 即使删了 {variable} 也不会让运行时炸。
    """

    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            return kwargs.get(key, "{" + key + "}")
        return super().get_value(key, args, kwargs)


_formatter = _SafeFormatter()


@dataclass
class ResolvedPrompt:
    value: str
    source: str  # 'db' | 'default'


class PromptStore:
    def __init__(self) -> None:
        self._db_cache: dict[str, str] = {}
        self._loaded = False

    async def reload(self) -> None:
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(select(PromptTemplate))).scalars().all()
        new_cache: dict[str, str] = {}
        for row in rows:
            if (row.value or "").strip():
                new_cache[row.key] = row.value
        self._db_cache = new_cache
        self._loaded = True

    def get(self, key: str) -> ResolvedPrompt:
        """返回模板的原始字符串 + 来源。"""
        if key in self._db_cache:
            return ResolvedPrompt(value=self._db_cache[key], source="db")
        meta = DEFAULT_PROMPTS.get(key)
        if meta is not None:
            return ResolvedPrompt(value=meta.default, source="default")
        return ResolvedPrompt(value="", source="default")

    def format(self, key: str, **variables: object) -> str:
        """get(key) + 把 {var} 占位符替换成传入值。缺失变量保留原样。"""
        template = self.get(key).value
        if not template:
            return ""
        try:
            return _formatter.format(template, **{k: str(v) for k, v in variables.items()})
        except (IndexError, ValueError) as exc:
            # {0} 或者 {1.2} 这种不合法的占位 → 把原文返回,日志记一笔
            print(f"[prompt_store] format failed for key={key}: {exc}")
            return template

    def format_lines(self, key: str, **variables: object) -> list[str]:
        """很多 prompt 在调用处当作 list[str] 用,这里直接按 \\n 切分。"""
        text = self.format(key, **variables)
        return [line for line in text.split("\n") if line.strip() or line == ""]

    async def upsert(
        self,
        db: AsyncSession,
        key: str,
        value: str,
        updated_by: str | None,
    ) -> PromptTemplate | None:
        """写一条 prompt。value 为空字符串 → 删除 DB 行,回退到代码默认。"""
        if key not in DEFAULT_PROMPTS:
            raise ValueError(f"未登记的 prompt key: {key}")
        existing = await db.get(PromptTemplate, key)
        if not value.strip():
            if existing is not None:
                await db.delete(existing)
            return None
        if existing is not None:
            existing.value = value
            existing.updated_at = now_utc()
            existing.updated_by = updated_by
            return existing
        new_row = PromptTemplate(key=key, value=value, updated_by=updated_by)
        db.add(new_row)
        return new_row

    def list_with_metadata(self) -> list[dict[str, object]]:
        """供后台 UI 列表展示:每个 prompt 的元数据 + 当前值 + 默认值 + 来源。"""
        items: list[dict[str, object]] = []
        for key, meta in DEFAULT_PROMPTS.items():
            resolved = self.get(key)
            items.append(
                {
                    "key": meta.key,
                    "label": meta.label,
                    "description": meta.description,
                    "variables": list(meta.variables),
                    "value": resolved.value,
                    "default": meta.default,
                    "source": resolved.source,
                    "is_overridden": resolved.source == "db",
                }
            )
        return items


_store: PromptStore | None = None


def init_prompt_store() -> PromptStore:
    global _store
    _store = PromptStore()
    return _store


def get_prompt_store() -> PromptStore:
    if _store is None:
        return init_prompt_store()
    return _store


def get_prompt_meta(key: str) -> PromptMeta | None:
    return DEFAULT_PROMPTS.get(key)
