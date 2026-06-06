"""messages 表加 next_actions 列,用于持久化 AI 推荐的下一步按钮

Revision ID: 0002_message_next_actions
Revises: 0001_initial
Create Date: 2026-05-18

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_message_next_actions"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("next_actions", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("messages", "next_actions")
