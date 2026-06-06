"""prompt_templates: 运行时可编辑的 prompt 模板表

Revision ID: 0008_prompt_templates
Revises: 0007_auth_and_settings
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_prompt_templates"
down_revision: str | Sequence[str] | None = "0007_auth_and_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_templates",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["app_users.id"], ondelete="SET NULL"),
    )


def downgrade() -> None:
    op.drop_table("prompt_templates")
