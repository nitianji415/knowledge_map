"""open-webSearch daemon 守护进程。

挂在 FastAPI lifespan 上,随主服务启动 / 关闭。
设计原则:
  * 失败不阻塞主 app(node 缺失、build 未编译、health 超时都只打 warning)
  * 已存在的 daemon(可能是用户自己手起的)会被识别并复用,不重复 spawn
  * 主 app 关闭时给 SIGTERM,5s 不退再 SIGKILL
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import Settings


class OpenWebSearchSupervisor:
    """spawn 并守护本地 open-webSearch daemon。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None
        self._log_task: asyncio.Task[Any] | None = None
        self._spawned_by_us = False

    @property
    def _base_url(self) -> str:
        return self.settings.open_websearch_url.rstrip("/")

    @property
    def _daemon_dir(self) -> Path:
        configured = (self.settings.open_websearch_dir or "external/open-webSearch").strip()
        path = Path(configured)
        if not path.is_absolute():
            path = self.settings.base_dir / configured
        return path

    async def start(self) -> None:
        if not self.settings.open_websearch_autostart:
            return
        if (self.settings.search_provider or "").strip().lower() != "open":
            # provider 不是 open,没必要起 daemon
            return

        # Pre-flight 1: 已经在跑?复用,不重复 spawn
        if await self._probe_health(timeout=1.5):
            print(f"[open-websearch] daemon 已在 {self._base_url} 上运行,复用现有进程")
            return

        # Pre-flight 2: build 是否就绪
        entry = self._daemon_dir / "build" / "index.js"
        if not entry.exists():
            print(
                f"[open-websearch] 未找到 {entry},跳过自动启动。\n"
                f"  首次使用请: cd {self._daemon_dir} && npm install && npm run build"
            )
            return

        # Pre-flight 3: node 是否可用
        node_bin = (self.settings.open_websearch_node_bin or "node").strip() or "node"
        # 把 URL 里的端口同步给 daemon,避免用户改 URL 后 daemon 还听默认 3210 对不上
        child_env = os.environ.copy()
        port = urlparse(self._base_url).port
        if port:
            child_env["OPEN_WEBSEARCH_DAEMON_PORT"] = str(port)
        try:
            self.process = await asyncio.create_subprocess_exec(
                node_bin,
                "build/index.js",
                "serve",
                cwd=str(self._daemon_dir),
                env=child_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            print(f"[open-websearch] 未找到 node 可执行文件 (`{node_bin}`),跳过自动启动")
            self.process = None
            return

        self._spawned_by_us = True
        self._log_task = asyncio.create_task(self._pump_logs())

        if await self._wait_for_health(timeout=30.0):
            print(f"[open-websearch] daemon 启动完成 @ {self._base_url}")
        else:
            print(
                "[open-websearch] daemon 30s 内 /health 未就绪,可能启动失败。"
                "app 仍可正常运行,但 chat 时网页搜索会回退为错误源。"
            )

    async def stop(self) -> None:
        if not self._spawned_by_us or self.process is None:
            return
        if self.process.returncode is not None:
            return  # 已经退出了
        try:
            self.process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
            await self.process.wait()
        if self._log_task and not self._log_task.done():
            self._log_task.cancel()
        print("[open-websearch] daemon 已停止")

    async def _probe_health(self, *, timeout: float) -> bool:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(f"{self._base_url}/health")
                return response.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    async def _wait_for_health(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # 进程可能已经异常退出,提前退出避免一直等
            if self.process and self.process.returncode is not None:
                return False
            if await self._probe_health(timeout=1.5):
                return True
            await asyncio.sleep(0.5)
        return False

    async def _pump_logs(self) -> None:
        if not self.process or not self.process.stdout:
            return
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip()
                if text:
                    print(f"[open-websearch] {text}")
        except asyncio.CancelledError:
            pass
