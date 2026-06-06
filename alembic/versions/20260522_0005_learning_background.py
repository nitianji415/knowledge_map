"""learning_sessions 加 learning_background 列

Revision ID: 0005_learning_background
Revises: 0004_message_peeks
Create Date: 2026-05-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_learning_background"
down_revision: str | Sequence[str] | None = "0004_message_peeks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "learning_sessions",
        sa.Column("learning_background", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("learning_sessions", "learning_background")
