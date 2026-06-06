"""initial schema: sessions / nodes / messages / events,含 collapsed 字段

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-17

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("field", sa.String(160), nullable=False),
        sa.Column("current_problem", sa.Text(), nullable=False),
        sa.Column("current_node_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.String(64), nullable=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("relevance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("importance", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("relevance_score", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("difficulty", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collapsed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_from_message_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["knowledge_nodes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_knowledge_nodes_session", "knowledge_nodes", ["session_id"])
    op.create_index("ix_knowledge_nodes_parent", "knowledge_nodes", ["parent_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["knowledge_nodes.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_messages_session", "messages", ["session_id"])

    op.create_table(
        "node_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["knowledge_nodes.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_node_events_session", "node_events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_node_events_session", table_name="node_events")
    op.drop_table("node_events")
    op.drop_index("ix_messages_session", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_knowledge_nodes_parent", table_name="knowledge_nodes")
    op.drop_index("ix_knowledge_nodes_session", table_name="knowledge_nodes")
    op.drop_table("knowledge_nodes")
    op.drop_table("learning_sessions")
