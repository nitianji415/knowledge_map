#!/usr/bin/env python3
"""兼容启动入口:`python3 server.py` 仍然能直接起服务。"""

from app.main import run

if __name__ == "__main__":
    run()
