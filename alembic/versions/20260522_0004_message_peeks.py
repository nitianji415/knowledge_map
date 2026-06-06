"""messages 加 peeks 列,存划词速览解释

Revision ID: 0004_message_peeks
Revises: 0003_message_highlights
Create Date: 2026-05-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_message_peeks"
down_revision: str | Sequence[str] | None = "0003_message_highlights"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("peeks", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("messages", "peeks")
