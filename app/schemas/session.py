"""学习会话相关 schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ThinkingMode
from app.schemas.message import MessageOut
from app.schemas.node import NodeOut


class TopicPreview(BaseModel):
    """预览阶段的一条主卡片(标题 + 一句话 summary)。

    custom=True 表示用户在预览框里手动新增的领域(非 AI 推荐)。
    """
    title: str = Field(min_length=1, max_length=40)
    summary: str = Field(default="", max_length=160)
    custom: bool = False


class PreviewTopicsIn(BaseModel):
    field: str = Field(min_length=1, max_length=160)
    current_problem: str = Field(min_length=1, max_length=2000)
    learning_background: str = Field(default="", max_length=2000)
    mode: ThinkingMode = "Lite"


class PreviewTopicsOut(BaseModel):
    topics: list[TopicPreview]


class CreateSessionIn(BaseModel):
    field: str = Field(min_length=1, max_length=160)
    current_problem: str = Field(min_length=1, max_length=2000)
    learning_background: str = Field(default="", max_length=2000)
    mode: ThinkingMode = "Lite"
    # 预览-确认流程:传 topics_override 则后端跳过 AI 拆树,直接用这些 title 建主干,
    # children 留空。客户端随后开 /grow-children SSE 拿子节点。
    # 不传则走老的"一步出整树"逻辑(向后兼容)。
    topics_override: list[TopicPreview] | None = None


class BackgroundQuestionIn(BaseModel):
    field: str = Field(min_length=1, max_length=160)
    current_problem: str = Field(min_length=1, max_length=2000)
    mode: ThinkingMode = "Lite"


class BackgroundQuestionOption(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=240)


class BackgroundQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=160)
    options: list[BackgroundQuestionOption] = Field(min_length=4, max_length=4)


class BackgroundQuestionOut(BaseModel):
    questions: list[BackgroundQuestion]


class BackgroundAnswered(BaseModel):
    """用户对一道背景诊断题的回答(question + 选择的 value)。"""
    question: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=400)


class BackgroundFollowupIn(BaseModel):
    field: str = Field(min_length=1, max_length=160)
    current_problem: str = Field(min_length=1, max_length=2000)
    mode: ThinkingMode = "Lite"
    # 用户到目前为止已经回答的所有问题(包含第一轮 + 之前几轮 follow-up)
    answered: list[BackgroundAnswered] = Field(min_length=1, max_length=20)
    # 已经追问过几轮——前端控制最多追问 2 轮
    follow_up_round: int = Field(default=0, ge=0, le=5)


class BackgroundFollowupOut(BaseModel):
    # AI 判断是否还需要追问;true 时给 questions,false 时表示信息已经够
    need_more: bool
    reason: str = Field(default="", max_length=200)
    questions: list[BackgroundQuestion] = Field(default_factory=list)


class CreateSessionOut(BaseModel):
    session_id: str
    current_node_id: str
    initial_nodes: list[NodeOut]
    messages: list[MessageOut]


class SessionListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    field: str
    current_problem: str
    learning_background: str
    current_node_id: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    node_count: int


class SessionsOut(BaseModel):
    sessions: list[SessionListItem]
