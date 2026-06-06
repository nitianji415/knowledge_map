"""open-webSearch 本地 daemon 客户端。

把本地 `open-webSearch` HTTP daemon (默认 127.0.0.1:3210) 的 /search 响应
规整成统一的 list[dict] (status/query/title/link/media/publish_date/content/refer),
下游 messages.search_sources 和前端 renderSearchSources 不用动。

daemon 启动方式见 external/open-webSearch:
    cd external/open-webSearch && npm run serve
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

import httpx

# description 字段里常见的"2026年5月14日"/"2026-05-14"/"2026/5/14" 三种写法
_DATE_RE = re.compile(r"(20\d{2})[-/年.]\s?(\d{1,2})[-/月.]\s?(\d{1,2})")
# open-webSearch 的 source 字段会把 domain 和原始 URL 拼在一起,
# 像 "sohu.comhttps://www.sohu.com/a/..." ——这里把 domain 切出来当 media
_MEDIA_RE = re.compile(r"^([^\s]+?)https?://")


def _parse_publish_date(desc: str) -> str:
    match = _DATE_RE.search(desc)
    if not match:
        return ""
    year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    return f"{year}-{month:02d}-{day:02d}"


def _clean_media(raw: str, fallback_url: str) -> str:
    raw = raw.strip()
    match = _MEDIA_RE.match(raw)
    if match:
        return match.group(1).strip()[:120]
    if raw:
        return raw[:120]
    host = urlparse(fallback_url).hostname or ""
    return host[:120]


def _unwrap_sogou_redirect(url: str) -> str:
    """sogou 返回的链接是 `/link?url=<base64ish>`,这里只做表层兜底:
    保留原 URL,不再额外发请求 follow——避免每次搜索都翻倍 RTT。
    后续如果需要真实 URL,在 fetch 时再展开。
    """
    return url


async def search(
    *,
    base_url: str,
    query: str,
    engines: list[str],
    search_mode: str = "auto",
    limit: int = 8,
    timeout: float = 20.0,
) -> list[dict[str, str]]:
    """调 daemon /search,返回 normalize 后的 sources。

    返回结构:status / query / title / link / media / publish_date / content / refer
    refer 复用为 "搜索引擎名"(sogou/duckduckgo/bing)。
    """
    body: dict[str, object] = {"query": query, "limit": limit, "searchMode": search_mode}
    if engines:
        body["engines"] = engines
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{base_url.rstrip('/')}/search", json=body)
        response.raise_for_status()
        payload = response.json()
    if payload.get("status") != "ok":
        err = payload.get("error") or payload
        raise RuntimeError(f"open-webSearch 响应异常: {err}")

    data = payload.get("data") or {}
    results = data.get("results") or []
    sources: list[dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        raw_url = str(item.get("url") or "").strip()
        url = unquote(_unwrap_sogou_redirect(raw_url))
        desc = str(item.get("description") or "").strip()
        if not title and not url and not desc:
            continue
        sources.append(
            {
                "status": "result",
                "query": query,
                "title": title[:240],
                "link": url[:1000],
                "media": _clean_media(str(item.get("source") or ""), url),
                "publish_date": _parse_publish_date(desc)[:80],
                "content": desc[:1200],
                "refer": str(item.get("engine") or "").strip()[:80],
            }
        )
    return sources
