"""AnySearch 统一搜索 API 客户端。

https://api.anysearch.com/v1/search
  - 可匿名调用(按 IP 限免费额度),也可带 Bearer key
  - 返回 results: [{title, url, description, content, source, published_at, ...}]
  - metadata 里有 routes_queried / search_time_ms,我们目前丢弃,只保留 results

把 AnySearch 的响应规整成和 open-webSearch 同构的 sources 列表,
下游 (messages.search_sources / 前端渲染) 不用动。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

# 把 RFC3339 时间 (2024-02-06T00:00:00Z) 规整成 YYYY-MM-DD,和别的 provider 保持一致
_RFC3339_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _format_publish_date(raw: str) -> str:
    if not raw:
        return ""
    match = _RFC3339_DATE_RE.match(raw.strip())
    return match.group(1) if match else raw[:80]


def _extract_media(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).hostname or ""
        # 去掉 www. 前缀,展示更干净
        if host.startswith("www."):
            host = host[4:]
        return host[:120]
    except Exception:  # noqa: BLE001
        return ""


def _pick_content(item: dict, char_limit: int = 2000) -> str:
    """AnySearch 同时给 description (简短摘要) 和 content (清洗后正文)。
    用 content 优先,落空回 description。char_limit 控制喂给 LLM 的最大字符数。
    """
    if char_limit <= 0:
        char_limit = 2000
    # 硬上限 10000:对齐 SearchSource.content 的 pydantic max_length,
    # 防止用户在设置页输入超大值时 MessageOut 验证失败抛 500
    if char_limit > 10000:
        char_limit = 10000
    content = str(item.get("content") or "").strip()
    if content:
        return content[:char_limit]
    description = str(item.get("description") or "").strip()
    return description[:char_limit]


async def search(
    *,
    base_url: str,
    query: str,
    api_key: str | None = None,
    max_results: int = 10,
    zone: str | None = None,
    language: str | None = None,
    timeout: float = 20.0,
    content_char_limit: int = 2000,
) -> list[dict[str, str]]:
    """调 AnySearch /v1/search,返回 normalize 后的 source 列表。

    api_key 可空 → 走匿名调用(按客户端 IP 限额);非空 → Bearer 认证。
    抛 RuntimeError 时调用方会把错误塞到一条 status=error 的 source 里给前端展示。
    """
    body: dict[str, object] = {"query": query, "max_results": max_results}
    if zone:
        body["zone"] = zone
    if language:
        body["language"] = language

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/search", json=body, headers=headers
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"anysearch 请求失败: {exc!r}") from exc

        if response.status_code >= 400:
            payload = {}
            try:
                payload = response.json()
            except Exception:  # noqa: BLE001
                pass
            symbol = str(payload.get("symbol") or "")
            message = str(payload.get("message") or response.text or "").strip()[:240]
            raise RuntimeError(
                f"anysearch HTTP {response.status_code} ({symbol or 'error'}): {message}"
            )

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"anysearch 响应非 JSON: {exc!r}") from exc

    # 实际响应结构和官方文档不一致 —— 文档说 results 在顶层,
    # 实际是 {code, message, data: {results, ...}}。两种都兼容,先看 data.results,
    # 落空再看顶层 results,避免哪天上游改回去也能工作。
    data_block = payload.get("data") if isinstance(payload.get("data"), dict) else None
    raw_results: list = []
    if data_block and isinstance(data_block.get("results"), list):
        raw_results = data_block["results"]
    elif isinstance(payload.get("results"), list):
        raw_results = payload["results"]

    # 如果响应里有业务错误码 (code != 0),抛上去让上层日志能看到
    business_code = payload.get("code")
    if business_code not in (None, 0) and not raw_results:
        symbol = payload.get("symbol") or "business_error"
        message = str(payload.get("message") or "")[:240]
        raise RuntimeError(f"anysearch 业务错误 code={business_code} ({symbol}): {message}")

    if not raw_results:
        return []

    sources: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = _pick_content(item, content_char_limit)
        if not title and not url and not content:
            continue
        sources.append(
            {
                "status": "result",
                "query": query,
                "title": title[:240],
                "link": url[:1000],
                "media": _extract_media(url),
                "publish_date": _format_publish_date(str(item.get("published_at") or "")),
                "content": content,
                # refer 写成 "anysearch:<source>",前端依然按搜索引擎名识别;
                # AnySearch 内部 source 字段有 web / news / code / academic 等
                "refer": f"anysearch:{(item.get('source') or 'web')}"[:80],
            }
        )
    return sources
