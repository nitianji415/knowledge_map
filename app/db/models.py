"""ORM 模型:learning_sessions / knowledge_nodes / messages / node_events。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

NODE_STATUSES = ("pending", "active", "completed", "skipped", "deepening", "paused")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class LearningSession(Base):
    __tablename__ = "learning_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("sess"))
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default="local_user")
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    field: Mapped[str] = mapped_column(String(160), nullable=False)
    current_problem: Mapped[str] = mapped_column(Text, nullable=False)
    learning_background: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 优化C:滚动摘要——把超出"最近 N 条"窗口的旧对话压成一段持续更新的摘要,
    # 既保住远期上下文、又不让历史无限堆进每轮 prompt。context_summary_count 记录
    # 已折叠进摘要的(窗口外)消息条数,用来增量更新而非每轮重算。
    context_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context_summary_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False
    )

    nodes: Mapped[list["KnowledgeNode"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    events: Mapped[list["NodeEvent"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"
    __table_args__ = (
        Index("ix_knowledge_nodes_session", "session_id"),
        Index("ix_knowledge_nodes_parent", "parent_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("node"))
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("learning_sessions.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    relevance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    relevance_score: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 方案A:同组兄弟之间的"前置依赖"——这张卡建立在哪几张【同父】卡片之上。
    # 空数组 = 与同组其它卡片并列、不分先后。前端据此算"建议学习顺序"。
    prerequisite_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 第一性原理"拆到底":True 表示这是触底的基础公理/最小单位,不再往下拆。
    is_fundamental: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 第一性原理父子边解释:存在 child 节点上,描述 child 为什么是 parent 的底层依赖。
    fp_relation: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    fp_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    collapsed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_from_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False
    )

    session: Mapped[LearningSession] = relationship(back_populates="nodes")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session", "session_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg"))
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("learning_sessions.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    next_actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    highlights: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    peeks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    search_sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

    session: Mapped[LearningSession] = relationship(back_populates="messages")


class NodeEvent(Base):
    __tablename__ = "node_events"
    __table_args__ = (Index("ix_node_events_session", "session_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("evt"))
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("learning_sessions.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

    session: Mapped[LearningSession] = relationship(back_populates="events")


# ----------------------------------------------------------------------
# 认证 + 应用设置 (Phase 2/3)

USER_ROLES = ("admin", "user")


class AppUser(Base):
    """部署里的人。当前 MVP 只暴露 admin 一个;表结构留多用户扩展空间。

    历史数据 (learning_sessions.user_id = 'local_user') 会通过 alembic 迁移
    seed 一行 id='local_user' 的 admin,无缝接管。
    """

    __tablename__ = "app_users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("user"))
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="admin")
    # 默认密码警示:还在用 ADMIN_PASSWORD env 的默认值时为 True,前端会挂一条 banner
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False
    )


class PromptTemplate(Base):
    """运行时可编辑的 prompt 模板。

    key 是业务 prompt 的稳定标识(例如 'subdivide.instructions'),value 是模板正文。
    代码里读时:DB 有值 → 用 DB 的;没有 → 回到 prompt_defaults.DEFAULT_PROMPTS。
    模板里用 {变量名} 占位,运行时 PromptStore.format(key, **vars) 安全替换。
    """

    __tablename__ = "prompt_templates"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )


class LlmUsageEvent(Base):
    """每次 LLM 调用的 token 用量,用来量化"哪条调用最贵"以指导上下文优化。

    在 AIClient.chat() 收口处统一写入,失败不阻塞主流程。
    purpose 标识调用来源(explain / subdivide / initial_map / refine_query ...)。
    cache_hit / cache_miss 来自 DeepSeek 的 prompt_cache_* 字段(其它 provider 没有则为 0),
    用来验证 prompt 前缀缓存(优化项 A)的命中率。
    """

    __tablename__ = "llm_usage_events"
    __table_args__ = (Index("ix_llm_usage_events_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("usage"))
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False, default="chat")
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_miss_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    web_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class AppSetting(Base):
    """运行时可改的应用配置(LLM provider / API key / 搜索路由 ...)。

    value 默认明文存(模型名、provider 名都不敏感);敏感字段(API key 类)用
    encrypted=True 标识,settings_store 会自动 Fernet 加解密。

    应用启动时先读 DB 覆盖到 LayeredSettings,运行时改了立刻生效(下一次请求看到)。
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )
