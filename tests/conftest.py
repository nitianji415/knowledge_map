"""测试 fixture:用 SQLite in-memory 起一个独立 engine,绕过 DeepSeek。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import base as db_base
from app.db.base import Base
from app.main import create_app
from app.routers.deps import get_service, reset_service_cache


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """每个测试用例独立 in-memory engine,避免互相污染。"""

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    # 强行禁用外部 LLM,避免出网。所有用例都走本地 fallback。
    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", None)
    # Phase 3:测试场景下整体关掉 auth,所有 Depends(get_current_user) 自动返回 local_user
    monkeypatch.setattr(settings, "auth_enabled", False)

    reset_service_cache()
    get_service()

    # 绕过 app.lifespan 的真实 init_engine,直接注入测试 session 工厂。
    monkeypatch.setattr(db_base, "_session_factory", session_factory)
    monkeypatch.setattr(db_base, "_engine", session_factory.kw["bind"])

    # Phase 2:layered settings 用环境兜底,DB 那一层是空的
    from app.services.prompt_store import init_prompt_store
    from app.services.settings_store import init_layered_settings

    init_layered_settings(settings)
    init_prompt_store()

    app = create_app()
    transport = ASGITransport(app=app)

    # 跳过 lifespan(它会试图重新 init_engine,因测试已注入会冲突)
    app.router.lifespan_context = _noop_lifespan

    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _noop_lifespan(app):
    class _Ctx:
        async def __aenter__(self_inner):
            return None

        async def __aexit__(self_inner, exc_type, exc, tb):
            return None

    return _Ctx()
