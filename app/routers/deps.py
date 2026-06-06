"""共享依赖。"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.services.ai import DeepSeekClient
from app.services.knowledge import KnowledgeMapService


@lru_cache(maxsize=1)
def get_service() -> KnowledgeMapService:
    settings = get_settings()
    return KnowledgeMapService(DeepSeekClient(settings))


def reset_service_cache() -> None:
    """测试 fixture 切 settings 时调用。"""

    get_service.cache_clear()
