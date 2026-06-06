#!/usr/bin/env python3
"""一键启动入口:启动服务并打开浏览器。"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import uvicorn

from app.core.config import get_settings


def _browser_url(host: str, port: int) -> str:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}"


def _open_browser_later(url: str) -> None:
    # 给 uvicorn 一点启动时间;如果首次启动稍慢,浏览器页刷新即可。
    timer = threading.Timer(1.2, lambda: webbrowser.open(url, new=2))
    timer.daemon = True
    timer.start()


def main() -> None:
    project_root = Path(__file__).resolve().parent
    settings = get_settings()
    url = _browser_url(settings.host, settings.port)
    print(f"AI 知识地图启动中: {url}")
    _open_browser_later(url)
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        app_dir=str(project_root),
    )


if __name__ == "__main__":
    main()
