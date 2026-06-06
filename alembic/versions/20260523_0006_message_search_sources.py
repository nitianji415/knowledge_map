"""messages 加 search_sources 列

Revision ID: 0006_message_search_sources
Revises: 0005_learning_background
Create Date: 2026-05-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_message_search_sources"
down_revision: str | Sequence[str] | None = "0005_learning_background"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("search_sources", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("messages", "search_sources")
