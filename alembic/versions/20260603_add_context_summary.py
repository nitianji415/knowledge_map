"""add rolling context summary to learning_sessions (优化C:窗口外历史压缩)

Revision ID: 20260603_add_context_summary
Revises: 20260603_add_llm_usage_events
Create Date: 2026-06-03

"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260603_add_context_summary"
down_revision = "20260603_add_llm_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("learning_sessions") as batch:
        batch.add_column(
            sa.Column("context_summary", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column(
                "context_summary_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("learning_sessions") as batch:
        batch.drop_column("context_summary_count")
        batch.drop_column("context_summary")
