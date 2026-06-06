#!/usr/bin/env bash
set -euo pipefail

# 用 SQLite in-memory 起一个临时 FastAPI 服务,跑核心 API 闭环。
# 如果想跑 Postgres 烟雾测试,设置 DATABASE_URL 后运行。

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${KNOWLEDGE_MAP_PORT:-8788}"
BASE_URL="http://127.0.0.1:${PORT}"
TMP_DB="${KNOWLEDGE_MAP_SMOKE_DB:-/tmp/knowledge_map_smoke.sqlite3}"
rm -f "${TMP_DB}"

export KNOWLEDGE_MAP_PORT="${PORT}"
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///${TMP_DB}}"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
    wait "${SERVER_PID}" >/dev/null 2>&1 || true
  fi
  rm -f "${TMP_DB}"
}
trap cleanup EXIT

alembic upgrade head >/tmp/knowledge_map_smoke_alembic.log 2>&1

python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" \
  >/tmp/knowledge_map_smoke.log 2>&1 &
SERVER_PID="$!"

for _ in $(seq 1 40); do
  if curl -fsS "${BASE_URL}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl -fsS "${BASE_URL}/api/health" >/dev/null

session_json="$(
  curl -fsS \
    -H "Content-Type: application/json" \
    -d '{"field":"测试维护结构","current_problem":"确认核心 API 正常"}' \
    "${BASE_URL}/api/sessions"
)"

session_id="$(printf '%s' "${session_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')"
node_id="$(printf '%s' "${session_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["current_node_id"])')"

curl -fsS "${BASE_URL}/api/sessions/${session_id}/tree" >/dev/null
curl -fsS "${BASE_URL}/api/sessions/${session_id}/messages" >/dev/null

stream_output="$(
  curl -fsS -N \
    -H "Content-Type: application/json" \
    -d "{\"message\":\"继续深入\",\"current_node_id\":\"${node_id}\"}" \
    "${BASE_URL}/api/sessions/${session_id}/messages/stream"
)"

printf '%s' "${stream_output}" | grep -q "event: done"

curl -fsS -X PATCH \
  -H "Content-Type: application/json" \
  -d '{"collapsed": true}' \
  "${BASE_URL}/api/nodes/${node_id}" >/dev/null

echo "Smoke test passed: ${BASE_URL}"
