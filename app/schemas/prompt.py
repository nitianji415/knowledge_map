"""Prompt 模板编辑 schema(admin 后台用)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PromptItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    description: str
    variables: list[str]
    value: str
    default: str
    source: Literal["db", "default"]
    is_overridden: bool


class PromptsOut(BaseModel):
    items: list[PromptItem]


class UpdatePromptsIn(BaseModel):
    """批量更新 {key: 新模板}。值为空字符串 → 删除 DB 覆盖,回退默认。"""

    updates: dict[str, str] = Field(default_factory=dict)


class ResetPromptOut(BaseModel):
    ok: bool = True
    key: str
