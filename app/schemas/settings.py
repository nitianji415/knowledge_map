"""运行时应用配置 schema(LLM key / 搜索路由 等)。

读出去的 secret 字段永远 mask 成 '***' + 末 4 位;只有写入时才接受明文。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 配置分组:UI 按这个顺序渲染,每组顶部带说明
SETTING_GROUPS: dict[str, dict[str, object]] = {
    "llm": {
        "title": "对话模型(必填)",
        "description": "走 OpenAI 兼容协议,默认指向 DeepSeek。只要 base_url 是 OpenAI 兼容的(DeepSeek / Moonshot / OpenRouter / 自建 vLLM ...)填进来就能用。",
        "order": 1,
    },
    "search": {
        "title": "网页搜索(可选)",
        "description": "决定 AI 回答时是否查网页拿最新信息。open=本地免费 daemon(默认即开即用);anysearch=聚合搜索,需要 API Key,效果更好。",
        "order": 2,
    },
    "advanced": {
        "title": "高级参数",
        "description": "默认值适合绝大多数场景。不熟悉就别动。",
        "order": 3,
    },
}


# UI 上可改的配置项白名单。新增 key 一定要登记到这里,否则 PATCH 会被拒。
# 每条字段:
#   sensitive   — True 时 DB 加密 + GET 时 mask 成末 4 位
#   label       — UI 上的字段名
#   description — 帮助说明,放在 input 下面给用户看
#   group       — 属于哪个分组(必须在 SETTING_GROUPS 里)
SETTING_KEYS: dict[str, dict[str, object]] = {
    # === 对话模型 (OpenAI 兼容) ===
    "LLM_API_KEY": {
        "sensitive": True,
        "label": "API Key",
        "description": "OpenAI 兼容服务的 API Key。DeepSeek 在 https://platform.deepseek.com 申请。",
        "group": "llm",
    },
    "LLM_MODEL": {
        "sensitive": False,
        "label": "模型名",
        "description": "默认 deepseek-chat(V3 通用)。也可填 deepseek-reasoner(R1 推理强)、moonshot-v1-8k 等。",
        "group": "llm",
    },
    "LLM_BASE_URL": {
        "sensitive": False,
        "label": "Base URL",
        "description": "OpenAI 兼容服务的 API 入口。默认 https://api.deepseek.com/v1。换别的服务把这里改掉即可。",
        "group": "llm",
    },
    # === 网页搜索 ===
    "SEARCH_PROVIDER": {
        "sensitive": False,
        "label": "搜索来源",
        "description": (
            "open=本地免费 daemon(默认,无需 key);"
            "anysearch=聚合多源 + 高质量(填 key 配额更高,失败自动回退到 open);"
            "off=完全关闭联网。"
        ),
        "group": "search",
    },
    "ANYSEARCH_API_KEY": {
        "sensitive": True,
        "label": "AnySearch API Key",
        "description": "留空走匿名 IP 限额(每日免费配额);填 key 享更高并发。SEARCH_PROVIDER=anysearch 时建议填。",
        "group": "search",
    },
    "ANYSEARCH_LIMIT": {
        "sensitive": False,
        "label": "AnySearch 返回结果数",
        "description": "单次搜索返回多少条结果。默认 10。条数越多 AI 看到的越全,但 prompt 体积越大。建议 5-20。",
        "group": "search",
    },
    "ANYSEARCH_CONTENT_LIMIT": {
        "sensitive": False,
        "label": "AnySearch 单条正文字符上限",
        "description": "每条结果喂给 AI 多少字符正文。默认 2000。调到 3000-5000 让 AI 看更完整页面,但 prompt 成本翻倍。上限 10000。",
        "group": "search",
    },
    # === 高级 ===
    "LLM_MAX_TOKENS": {
        "sensitive": False,
        "label": "LLM 单次最大输出 tokens",
        "description": "AI 单次回答的长度上限。默认 8192(Zen 模式长答案的安全余量)。降低能省钱但 Zen 模式可能被截断。",
        "group": "advanced",
    },
}


class SettingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    description: str = ""  # 帮助文本,UI 显示在 input 下面
    group: str = "advanced"  # 分组键 (对应 SETTING_GROUPS)
    value: str  # 敏感字段 GET 时返回 mask 形式 '***1234' (末 4 位明文)
    sensitive: bool
    is_set: bool  # 是否有非空值(包括来自 env 的)
    source: Literal["db", "env", "default"]  # 这个值从哪里来


class SettingGroup(BaseModel):
    """配置分组的元数据,前端按 order 渲染分组 + 标题 + 说明。"""

    key: str
    title: str
    description: str
    order: int


class SettingsOut(BaseModel):
    items: list[SettingItem]
    groups: list[SettingGroup] = []


class UpdateSettingsIn(BaseModel):
    """前端传 {key: 新明文}。值为空字符串表示删除 DB 里的覆盖(回退到 env / 默认)。"""

    updates: dict[str, str] = Field(default_factory=dict)


class TestConnectionIn(BaseModel):
    """用表单里临时填的 key/model/base_url 跑一次最便宜的 ping,不写库。"""

    api_key: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=80)
    base_url: str | None = Field(default=None, max_length=200)


class TestConnectionOut(BaseModel):
    ok: bool
    detail: str = ""
    latency_ms: int | None = None
