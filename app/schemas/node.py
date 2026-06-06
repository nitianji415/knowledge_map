"""节点相关 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NodeStatus = Literal["pending", "active", "completed", "skipped", "deepening", "paused"]


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    parent_id: str | None
    title: str
    summary: str
    content: str
    status: NodeStatus
    relevance: int
    importance: int
    relevance_score: int
    difficulty: int
    depth: int
    sort_order: int
    prerequisite_ids: list[str] = Field(default_factory=list)
    is_fundamental: bool = False
    fp_relation: str = ""
    fp_reason: str = ""
    collapsed: bool
    created_from_message_id: str | None
    created_at: datetime
    updated_at: datetime


class NodesOut(BaseModel):
    nodes: list[NodeOut]


class UpdateNodeIn(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    summary: str | None = Field(default=None, max_length=400)
    content: str | None = None
    status: NodeStatus | None = None
    importance: int | None = Field(default=None, ge=1, le=3)
    relevance_score: int | None = Field(default=None, ge=1, le=3)
    difficulty: int | None = Field(default=None, ge=1, le=3)
    collapsed: bool | None = None


class UpdateNodeOut(BaseModel):
    node: NodeOut
    nodes: list[NodeOut]


class NodeSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=120)
    # 默认返回前 5,避免列表太长占用右侧画布,前端的容器也按 5 设计
    limit: int = Field(default=5, ge=1, le=12)


class NodeSearchHit(BaseModel):
    node_id: str
    title: str
    summary: str
    score: int = Field(ge=1, le=3)
    reason: str


class NodeSearchOut(BaseModel):
    query: str
    results: list[NodeSearchHit]
