"""messages 加 highlights 列,存用户划词高亮

Revision ID: 0003_message_highlights
Revises: 0002_message_next_actions
Create Date: 2026-05-19

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_message_highlights"
down_revision: str | Sequence[str] | None = "0002_message_next_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("highlights", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("messages", "highlights")
