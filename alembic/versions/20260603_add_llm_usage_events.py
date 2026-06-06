"""add llm_usage_events (per-call token usage for context-cost analysis)

Revision ID: 20260603_add_llm_usage_events
Revises: 20260530_fp_edge_explanations
Create Date: 2026-06-03

"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260603_add_llm_usage_events"
down_revision = "20260530_fp_edge_explanations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("purpose", sa.String(length=40), nullable=False, server_default="chat"),
        sa.Column("model", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_miss_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("web_search", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_usage_events_created", "llm_usage_events", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_llm_usage_events_created", table_name="llm_usage_events")
    op.drop_table("llm_usage_events")
