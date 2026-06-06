"""add first-principles edge explanation fields

Revision ID: 20260530_fp_edge_explanations
Revises: 20260530_add_is_fundamental
Create Date: 2026-05-30

"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260530_fp_edge_explanations"
down_revision = "20260530_add_is_fundamental"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_nodes") as batch:
        batch.add_column(sa.Column("fp_relation", sa.String(length=80), nullable=False, server_default=""))
        batch.add_column(sa.Column("fp_reason", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_nodes") as batch:
        batch.drop_column("fp_reason")
        batch.drop_column("fp_relation")
