"""异步 SQLAlchemy 引擎与 session 工厂。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def init_engine(database_url: str, **kwargs: Any) -> AsyncEngine:
    """初始化全局 engine 和 session 工厂。重复调用会先关旧的再建新的。"""

    global _engine, _session_factory

    if _engine is not None:
        # 多次启动场景:测试 fixture 切库时,先彻底释放旧连接。
        raise RuntimeError("Engine already initialised; call shutdown_engine() first.")

    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_async_engine(
        database_url,
        echo=False,
        future=True,
        connect_args=connect_args,
        **kwargs,
    )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        autoflush=False,
        class_=AsyncSession,
    )
    return _engine


async def shutdown_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Engine not initialised; call init_engine() first.")
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖:每次请求开一个 session,出错自动 rollback。"""

    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
