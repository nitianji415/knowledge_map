"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.db.base import get_session_factory, init_engine, shutdown_engine
from app.routers import auth, health, messages, nodes, prompts, sessions, web_search
from app.routers import settings as settings_router
from app.services.auth import ensure_admin_seeded
from app.services.prompt_store import init_prompt_store
from app.services.settings_store import init_layered_settings
from app.services.web_search_daemon import OpenWebSearchSupervisor


def _warn_insecure_defaults(settings) -> None:
    """启动自检:仍在用内置 dev 默认密钥/密码时,打一条醒目警告。

    SETTINGS_SECRET 用来加密存进 DB 的 API key——若是默认值,任何拿到 DB 的人都能解密。
    生产部署务必通过环境变量改掉这三项。"""
    issues = []
    if settings.settings_secret.endswith("please-change"):
        issues.append("SETTINGS_SECRET(用于加密 API key,默认值会让密钥可被任何人解密)")
    if settings.jwt_secret.endswith("please-change"):
        issues.append("JWT_SECRET(用于签发登录 token)")
    if settings.admin_password == "admin":
        issues.append("ADMIN_PASSWORD(默认 admin/admin)")
    if not issues:
        return
    bar = "!" * 64
    print(f"\n{bar}")
    print("[knowledge_map] 安全警告:以下配置仍是内置默认值,生产环境必须改:")
    for item in issues:
        print(f"  - {item}")
    print("  改法:在 .env 或环境变量里设置上述项后重启。详见 README「安全」一节。")
    print(f"{bar}\n")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    _warn_insecure_defaults(settings)
    init_engine(settings.database_url)

    # 启动顺序很重要:
    #   1. engine 起好后,先 seed admin 用户 + load app_settings 到内存
    #   2. 才能拉 open-websearch daemon(它会 fork 子进程,不依赖 DB)
    layered = init_layered_settings(settings)
    prompt_store = init_prompt_store()
    factory = get_session_factory()
    async with factory() as db:
        await ensure_admin_seeded(db, settings)
        await db.commit()
    await layered.reload()
    await prompt_store.reload()

    daemon = OpenWebSearchSupervisor(settings)
    try:
        await daemon.start()
        yield
    finally:
        await daemon.stop()
        await shutdown_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI 知识地图", version="0.4.0", lifespan=lifespan)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(settings_router.router)
    app.include_router(prompts.router)
    app.include_router(sessions.router)
    app.include_router(messages.router)
    app.include_router(messages.messages_router)
    app.include_router(nodes.router)
    app.include_router(nodes.session_node_router)
    app.include_router(web_search.router)

    static_dir = settings.static_dir
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
