#!/usr/bin/env python3
"""把旧 SQLite (data/knowledge_map.sqlite3) 数据导入到目标 DATABASE_URL。

用法:
  1) docker compose up -d postgres
  2) alembic upgrade head
  3) python scripts/migrate_sqlite_to_postgres.py [--source data/knowledge_map.sqlite3]

幂等保证:目标库已有同 id 行会跳过,不覆盖。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402

TABLES = ["learning_sessions", "knowledge_nodes", "messages", "node_events"]


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def _columns(engine: AsyncEngine, table: str) -> list[str]:
    async with engine.connect() as conn:

        def _read(sync_conn) -> list[str]:
            return [c["name"] for c in inspect(sync_conn).get_columns(table)]

        return await conn.run_sync(_read)


async def migrate(source_path: Path, target_url: str) -> None:
    if not source_path.exists():
        raise SystemExit(f"找不到旧 SQLite: {source_path}")
    sqlite_conn = sqlite3.connect(source_path)
    sqlite_conn.row_factory = sqlite3.Row

    target_engine = create_async_engine(target_url, future=True)
    column_map = {table: await _columns(target_engine, table) for table in TABLES}

    inserted = {table: 0 for table in TABLES}
    skipped = {table: 0 for table in TABLES}

    async with target_engine.begin() as conn:
        for table in TABLES:
            cur = sqlite_conn.execute(f"SELECT * FROM {table}")
            rows = cur.fetchall()
            target_cols = column_map[table]
            for row in rows:
                source = {key: row[key] for key in row.keys()}
                payload: dict[str, object | None] = {}
                for col in target_cols:
                    value = source.get(col)
                    if col in {"created_at", "updated_at"}:
                        value = _parse_dt(value)
                    elif col == "collapsed" and value is None:
                        value = False
                    elif col == "payload" and isinstance(value, str):
                        # node_events.payload_json 旧字段
                        try:
                            value = json.loads(value)
                        except json.JSONDecodeError:
                            value = {}
                    payload[col] = value

                if "payload" in target_cols and "payload" not in payload:
                    raw = source.get("payload_json")
                    try:
                        payload["payload"] = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        payload["payload"] = {}

                pk_value = payload.get("id")
                exists = await conn.execute(
                    text(f"SELECT 1 FROM {table} WHERE id = :id"), {"id": pk_value}
                )
                if exists.first():
                    skipped[table] += 1
                    continue

                cols_sql = ", ".join(payload.keys())
                params_sql = ", ".join(f":{k}" for k in payload.keys())
                await conn.execute(
                    text(f"INSERT INTO {table} ({cols_sql}) VALUES ({params_sql})"), payload
                )
                inserted[table] += 1

    await target_engine.dispose()
    sqlite_conn.close()

    for table in TABLES:
        print(f"{table}: inserted={inserted[table]} skipped={skipped[table]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "data" / "knowledge_map.sqlite3",
        help="旧 SQLite 文件路径",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="目标 DATABASE_URL,默认读 .env 里的配置",
    )
    args = parser.parse_args()

    target_url = args.target or get_settings().database_url
    print(f"source={args.source}\ntarget={target_url}\n")
    asyncio.run(migrate(args.source, target_url))


if __name__ == "__main__":
    main()
