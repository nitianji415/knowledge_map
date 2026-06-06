"""运行时配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    host: str = Field(default="127.0.0.1", validation_alias="KNOWLEDGE_MAP_HOST")
    port: int = Field(default=8765, validation_alias="KNOWLEDGE_MAP_PORT")

    database_url: str = Field(
        default="postgresql+asyncpg://knowledge_map:knowledge_map@127.0.0.1:5432/knowledge_map",
        validation_alias="DATABASE_URL",
    )
    test_database_url: str = Field(
        default="sqlite+aiosqlite:///:memory:",
        validation_alias="TEST_DATABASE_URL",
    )

    # 认证 + 加密。生产部署必须从 env 显式设置;留默认值只是为了 dev 不卡住。
    # JWT 签名 secret —— 改了之后所有现有 token 立刻失效
    jwt_secret: str = Field(
        default="knowledge-map-dev-jwt-secret-please-change",
        validation_alias="JWT_SECRET",
    )
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    jwt_expires_hours: int = Field(default=24 * 14, validation_alias="JWT_EXPIRES_HOURS")
    # API key 加密 secret —— 用来派生 Fernet key,加密存到 DB 的 LLM key 都靠它
    # 改了之后老的 DB 里加密值会解不出来,所以要么不改要么手动 re-encrypt
    settings_secret: str = Field(
        default="knowledge-map-dev-settings-secret-please-change",
        validation_alias="SETTINGS_SECRET",
    )
    # 首次启动如果 app_users 表为空,自动 seed 一个 admin。默认密码 'admin',会在日志打 WARNING
    admin_username: str = Field(default="admin", validation_alias="ADMIN_USERNAME")
    admin_password: str = Field(default="admin", validation_alias="ADMIN_PASSWORD")
    # 关掉这个就完全跳过登录(仅给 pytest 用,生产严禁)
    auth_enabled: bool = Field(default=True, validation_alias="AUTH_ENABLED")

    # === LLM (OpenAI 兼容协议) ===
    # 全系统只走一条 OpenAI 兼容路径,任何走这协议的服务(DeepSeek / Moonshot / 自建 vLLM / OpenRouter / ...)
    # 改 base_url + model 就能用。默认指向 DeepSeek 因为最普及、价格低。
    llm_api_key: str | None = Field(default=None, validation_alias="LLM_API_KEY")
    llm_model: str = Field(default="deepseek-chat", validation_alias="LLM_MODEL")
    llm_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        validation_alias="LLM_BASE_URL",
    )
    # 单次回答的输出上限,默认 8192 是 Zen 长答案的安全余量
    llm_max_tokens: int = Field(default=8192, validation_alias="LLM_MAX_TOKENS")
    llm_log_responses: bool = Field(default=True, validation_alias="LLM_LOG_RESPONSES")
    llm_log_max_chars: int = Field(default=4000, validation_alias="LLM_LOG_MAX_CHARS")

    # 网页检索路由: open | anysearch | off
    #   open       本地 open-webSearch daemon (免费 sogou/duckduckgo)
    #   anysearch  AnySearch 统一搜索 (聚合多源,可匿名 IP 限额 / 也可带 Bearer key)
    #   off        关闭网页搜索
    search_provider: str = Field(default="open", validation_alias="SEARCH_PROVIDER")

    # open-webSearch daemon:本地 npm run serve 起的 HTTP 服务,免费、免 Key。
    # engines 是逗号分隔的引擎名;实测 sogou+duckduckgo 对中文实体最稳定。
    # search_mode: request(纯 HTTP,快但 Bing/百度会被风控) | auto | playwright(浏览器渲染,慢)
    open_websearch_url: str = Field(
        default="http://127.0.0.1:3210",
        validation_alias="OPEN_WEBSEARCH_URL",
    )
    open_websearch_engines: str = Field(
        default="sogou,duckduckgo",
        validation_alias="OPEN_WEBSEARCH_ENGINES",
    )
    open_websearch_search_mode: str = Field(
        default="auto", validation_alias="OPEN_WEBSEARCH_SEARCH_MODE"
    )
    open_websearch_limit: int = Field(
        default=8, validation_alias="OPEN_WEBSEARCH_LIMIT"
    )
    open_websearch_timeout: float = Field(
        default=20.0, validation_alias="OPEN_WEBSEARCH_TIMEOUT"
    )
    # 自动随主 app 启动 daemon。docker-compose 或外部 supervisor 管理时设 false。
    open_websearch_autostart: bool = Field(
        default=True, validation_alias="OPEN_WEBSEARCH_AUTOSTART"
    )
    # daemon 源码目录;相对路径以项目根目录为基准
    open_websearch_dir: str = Field(
        default="external/open-webSearch",
        validation_alias="OPEN_WEBSEARCH_DIR",
    )
    # 用于跨平台/nvm 等场景显式指定 node 二进制路径
    open_websearch_node_bin: str = Field(
        default="node", validation_alias="OPEN_WEBSEARCH_NODE_BIN"
    )

    # AnySearch:https://api.anysearch.com
    # api_key 留空走匿名(按 IP 限免费额度);填了走 Bearer,享更高并发和付费额度。
    anysearch_api_key: str | None = Field(default=None, validation_alias="ANYSEARCH_API_KEY")
    anysearch_base_url: str = Field(
        default="https://api.anysearch.com",
        validation_alias="ANYSEARCH_BASE_URL",
    )
    anysearch_limit: int = Field(default=10, validation_alias="ANYSEARCH_LIMIT")
    anysearch_timeout: float = Field(default=20.0, validation_alias="ANYSEARCH_TIMEOUT")
    # 每条结果正文 (content/description) 喂给 LLM 的字符上限。
    # 默认 2000 (比原始 1200 多一倍,主流模型 128K 输入完全装得下)。
    # 加大 → AI 看到的页面更完整、回答更深;但 prompt 成本翻倍。
    anysearch_content_char_limit: int = Field(
        default=2000, validation_alias="ANYSEARCH_CONTENT_LIMIT"
    )
    # 留空 = 让 AnySearch 自动判断;填 'cn' / 'intl' 强制走对应区域
    anysearch_zone: str = Field(default="", validation_alias="ANYSEARCH_ZONE")
    # 留空 = 不显式传;典型 'zh-CN' / 'en'
    anysearch_language: str = Field(default="", validation_alias="ANYSEARCH_LANGUAGE")

    @property
    def base_dir(self) -> Path:
        return BASE_DIR

    @property
    def static_dir(self) -> Path:
        return STATIC_DIR

    @property
    def data_dir(self) -> Path:
        return DATA_DIR


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
