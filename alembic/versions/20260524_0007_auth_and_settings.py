"""auth + app_settings: 加 app_users / app_settings 两张表,seed 兼容历史的 local_user

Revision ID: 0007_auth_and_settings
Revises: 0006_message_search_sources
Create Date: 2026-05-24

历史数据 learning_sessions.user_id = 'local_user' 是硬编码的字符串,不是 FK。
这一版新建 app_users,我们在迁移里 seed 一行 id='local_user' 的占位用户(无密码,
启动时 lifespan 会按 ADMIN_USERNAME/ADMIN_PASSWORD 重新 upsert)。

这样:
  * 老 sessions 自然挂到 admin 名下
  * 不用回填 learning_sessions
  * 多用户扩展时再加 learning_sessions.user_id 的 FK 约束
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_auth_and_settings"
down_revision: str | Sequence[str] | None = "0006_message_search_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        # 占位 hash —— lifespan seed 会改成真正的 bcrypt hash
        sa.Column("password_hash", sa.String(255), nullable=False, server_default=""),
        sa.Column("role", sa.String(20), nullable=False, server_default="admin"),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["app_users.id"], ondelete="SET NULL"),
    )

    # 占位 admin —— 接管历史的 user_id='local_user' 数据,启动时 lifespan 会改密码 + 改 username
    op.execute(
        "INSERT INTO app_users (id, username, password_hash, role, must_change_password) "
        "VALUES ('local_user', 'admin', '', 'admin', TRUE)"
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("app_users")
