"""消息相关 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import MessageIntent, NextActionKind, ThinkingMode
from app.schemas.node import NodeOut

MessageRole = Literal["user", "assistant", "system"]


class NextAction(BaseModel):
    kind: NextActionKind
    label: str = Field(min_length=1, max_length=40)
    payload: str = Field(min_length=1, max_length=200)
    target_node_id: str | None = None
    # "next_step" = 后端注入的"下一个知识点"按钮(前端做特殊样式 + 自动导航)
    kind_hint: str | None = Field(default=None, max_length=24)


class Highlight(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=600)


class PeekFollowup(BaseModel):
    id: str | None = None
    question: str = Field(min_length=1, max_length=600)
    answer: str = Field(min_length=1, max_length=2000)


class PeekAnchor(BaseModel):
    id: str
    # null = 锚在消息正文(老逻辑);非 null = 锚在某个 peek 的 answer 上(嵌套速览)。
    # 多层嵌套通过沿 parent_peek_id 上溯构成一棵树。
    parent_peek_id: str | None = None
    # answer = 锚在父 peek 首段回答; followup = 锚在父 peek 的某条追问回答。
    source_kind: str = "answer"
    source_followup_id: str | None = None
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=600)
    answer: str = Field(min_length=1, max_length=2000)
    status: str = "answered"
    promoted_node_id: str | None = None
    followups: list[PeekFollowup] = Field(default_factory=list)


class SearchSource(BaseModel):
    status: str = Field(default="result", max_length=40)
    query: str = Field(default="", max_length=120)
    title: str = Field(default="", max_length=240)
    link: str = Field(default="", max_length=1000)
    media: str = Field(default="", max_length=120)
    publish_date: str = Field(default="", max_length=80)
    # 上限提到 10000 —— 用户可在设置页把 ANYSEARCH_CONTENT_LIMIT 调到 5000+,
    # 之前 1200 写死会触发 pydantic string_too_long → MessageOut 验证失败 → 500
    content: str = Field(default="", max_length=10000)
    refer: str = Field(default="", max_length=80)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    node_id: str | None
    role: MessageRole
    content: str
    next_actions: list[NextAction] = Field(default_factory=list)
    highlights: list[Highlight] = Field(default_factory=list)
    peeks: list[PeekAnchor] = Field(default_factory=list)
    search_sources: list[SearchSource] = Field(default_factory=list)
    created_at: datetime


class MessagesOut(BaseModel):
    messages: list[MessageOut]


class SendMessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    current_node_id: str | None = None
    mode: ThinkingMode = "Lite"
    intent: MessageIntent = "auto"
    promoted_title: str | None = Field(default=None, max_length=80)
    # 当 intent=subdivide 时,可以指定"按哪个角度拆":构成/步骤/类型/对比/因果/...
    # 也支持用户自定义短句(如"按客群拆")
    subdivision_angle: str | None = Field(default=None, max_length=80)


class DeepReanswerIn(BaseModel):
    mode: ThinkingMode = "Lite"


class SubdivisionOption(BaseModel):
    angle: str = Field(min_length=1, max_length=24)
    label: str = Field(min_length=1, max_length=40)
    rationale: str = Field(min_length=1, max_length=120)


class SubdivisionCaution(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    rationale: str = Field(min_length=1, max_length=400)


class SubdivisionOptionsIn(BaseModel):
    mode: ThinkingMode = "Lite"


class SubdivisionOptionsOut(BaseModel):
    options: list[SubdivisionOption]
    # caution 只在 AI 觉得当前节点不该再拆时返回;前端据此决定渲不渲染那一块
    caution: SubdivisionCaution | None = None


class CautionNoteIn(BaseModel):
    rationale: str = Field(min_length=1, max_length=600)
    mode: ThinkingMode = "Lite"


class CautionNoteOut(BaseModel):
    message: "MessageOut"


class MultiAngleSubdivideIn(BaseModel):
    mode: ThinkingMode = "Lite"
    # 沿用浮层里 AI 已经选好的角度,不重新挑;前端只传 angle + label 就够
    angles: list[SubdivisionOption] = Field(min_length=2, max_length=4)


class MultiAngleSubdivideOut(BaseModel):
    reply: str
    current_node_id: str
    created_node_ids: list[str]
    nodes: list[NodeOut]
    messages: list["MessageOut"]


class UpdateHighlightsIn(BaseModel):
    highlights: list[Highlight]


class CreatePeekIn(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=600)
    mode: ThinkingMode = "Lite"
    # null = 在消息正文上划词(默认);设了就是在某个 peek 卡片的 answer 上划词,
    # start/end 默认相对父 peek 的 answer;source_kind=followup 时相对指定追问 answer。
    parent_peek_id: str | None = None
    source_kind: str = Field(default="answer", max_length=20)
    source_followup_id: str | None = Field(default=None, max_length=64)


class CreatePeekFollowupIn(BaseModel):
    question: str = Field(min_length=1, max_length=600)
    mode: ThinkingMode = "Lite"
