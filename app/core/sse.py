"""SSE 工具:把回复按句切片,边发边间隔,避免一次性 dump。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

# 内容在后端早已整段生成完,这里只是切片推给前端做"打字机"视觉。
# 之前每片 sleep 25ms,几十片就白白多等 ~1s。设 0:切片照旧、不再人工等待。
CHUNK_DELAY_SECONDS = 0.0
MAX_BUFFER = 24
LINE_BREAKS = "。！？\n"


def split_stream(text: str) -> list[str]:
    chunks: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char in LINE_BREAKS or len(buffer) >= MAX_BUFFER:
            chunks.append(buffer)
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


def format_event(event: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


async def stream_reply(reply: str, done_payload: dict[str, Any]) -> AsyncIterator[bytes]:
    for chunk in split_stream(reply):
        yield format_event("token", {"text": chunk})
        await asyncio.sleep(CHUNK_DELAY_SECONDS)
    yield format_event("done", done_payload)
