"""把 ZHIPU_* / DEEPSEEK_* / LLM_PROVIDER 的 app_settings 行,
合并成单一 LLM_API_KEY / LLM_MODEL / LLM_BASE_URL,然后删掉旧行。

Revision ID: 0009_unify_llm_settings
Revises: 0008_prompt_templates
Create Date: 2026-05-25

幂等:重复执行只看缺啥就补啥;敏感字段密文(Fernet)随 key 改名一起搬,
不需要解密,因为 Fernet 主密钥是从 SETTINGS_SECRET 派生的,与行 key 名无关。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_unify_llm_settings"
down_revision: str | Sequence[str] | None = "0008_prompt_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (新 key,候选旧 key 列表 —— 第一个存在的就被采用)
_RENAMES: list[tuple[str, list[str]]] = [
    ("LLM_API_KEY", ["DEEPSEEK_API_KEY", "ZHIPU_API_KEY"]),
    ("LLM_MODEL", ["DEEPSEEK_MODEL", "ZHIPU_MODEL"]),
    ("LLM_BASE_URL", ["DEEPSEEK_BASE_URL", "ZHIPU_BASE_URL"]),
]

# 单纯删掉的旧 key
_DEAD_KEYS = [
    "LLM_PROVIDER",
    "ZHIPU_WEB_SEARCH_ENABLED",
    "ZHIPU_SEARCH_ENGINE",
    "ZHIPU_WEB_SEARCH_URL",
]


def upgrade() -> None:
    conn = op.get_bind()
    for new_key, old_keys in _RENAMES:
        # 新 key 已有值就跳过
        row = conn.exec_driver_sql(
            "SELECT key FROM app_settings WHERE key = :k", {"k": new_key}
        ).first() if False else None
        existing = conn.execute(
            __sa_text("SELECT 1 FROM app_settings WHERE key = :k"), {"k": new_key}
        ).first()
        if existing:
            # 已经存在新行;清掉所有候选老行
            for old in old_keys:
                conn.execute(
                    __sa_text("DELETE FROM app_settings WHERE key = :k"), {"k": old}
                )
            continue
        # 找第一个存在的老行,改名
        for old in old_keys:
            row = conn.execute(
                __sa_text("SELECT 1 FROM app_settings WHERE key = :k"), {"k": old}
            ).first()
            if row:
                conn.execute(
                    __sa_text("UPDATE app_settings SET key = :new WHERE key = :old"),
                    {"new": new_key, "old": old},
                )
                break
        # 剩下的候选老行删掉
        for old in old_keys:
            conn.execute(
                __sa_text("DELETE FROM app_settings WHERE key = :k"), {"k": old}
            )
    # 清掉纯死键
    for dead in _DEAD_KEYS:
        conn.execute(__sa_text("DELETE FROM app_settings WHERE key = :k"), {"k": dead})


def downgrade() -> None:
    # 不可逆:零信息把 LLM_* 拆回 ZHIPU_*/DEEPSEEK_*,所以这里只清掉新 key
    conn = op.get_bind()
    for new_key, _ in _RENAMES:
        conn.execute(__sa_text("DELETE FROM app_settings WHERE key = :k"), {"k": new_key})


def __sa_text(sql: str):
    # 局部 import 避免顶部依赖;Alembic env 已确保 sqlalchemy 可用
    from sqlalchemy import text

    return text(sql)
