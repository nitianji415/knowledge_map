#!/usr/bin/env bash
# 容器启动:等 DB 起来 → alembic upgrade head → 起 uvicorn
set -euo pipefail

echo "[entrypoint] DATABASE_URL=${DATABASE_URL:-(unset)}"

# 等 Postgres 起来。compose 的 depends_on healthcheck 已经保证大致就绪,
# 这里再加一个 30s 的兜底,避免冷启动慢的机器上抢跑。
if [[ "${DATABASE_URL:-}" == postgresql* ]]; then
  python3 - <<'PY'
import asyncio, os, sys, time
from sqlalchemy.ext.asyncio import create_async_engine

url = os.environ["DATABASE_URL"]
deadline = time.monotonic() + 30
async def probe():
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("SELECT 1")
    finally:
        await engine.dispose()

while True:
    try:
        asyncio.run(probe())
        break
    except Exception as exc:
        if time.monotonic() > deadline:
            print(f"[entrypoint] DB 30s 内仍不可用: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(1.0)
print("[entrypoint] DB 就绪")
PY
fi

# 跑迁移到 head。重复执行幂等,首次启动会建表。
echo "[entrypoint] 跑 alembic upgrade head"
alembic upgrade head

# 起服务
exec uvicorn app.main:app \
  --host "${KNOWLEDGE_MAP_HOST:-0.0.0.0}" \
  --port "${KNOWLEDGE_MAP_PORT:-8765}" \
  --proxy-headers \
  --forwarded-allow-ips="*"
