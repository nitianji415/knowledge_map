"""add is_fundamental to knowledge_nodes (第一性原理拆到底:触底标记)

Revision ID: 20260530_add_is_fundamental
Revises: 20260530_add_prerequisite_ids
Create Date: 2026-05-30

"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260530_add_is_fundamental"
down_revision = "20260530_add_prerequisite_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_nodes") as batch:
        batch.add_column(
            sa.Column(
                "is_fundamental",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_nodes") as batch:
        batch.drop_column("is_fundamental")
