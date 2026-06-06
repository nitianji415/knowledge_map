"""add prerequisite_ids to knowledge_nodes (方案A 同组兄弟依赖)

Revision ID: 20260530_add_prerequisite_ids
Revises: 0009_unify_llm_settings
Create Date: 2026-05-30

"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260530_add_prerequisite_ids"
down_revision = "0009_unify_llm_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 同组兄弟之间的前置依赖,JSON 数组存兄弟节点 id。
    # server_default='[]' 让历史行平滑获得空数组(= 并列,不分先后)。
    with op.batch_alter_table("knowledge_nodes") as batch:
        batch.add_column(
            sa.Column(
                "prerequisite_ids",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_nodes") as batch:
        batch.drop_column("prerequisite_ids")
