"""划词联网搜索接口的请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.message import SearchSource


class WebSearchIn(BaseModel):
    """用户划了一段词,直接拿这段词去联网检索。"""

    query: str = Field(min_length=1, max_length=400)
    # 默认拉 10 条;前端展示时会列在 popover 里
    limit: int = Field(default=10, ge=1, le=50)


class WebSearchOut(BaseModel):
    query: str
    sources: list[SearchSource]
