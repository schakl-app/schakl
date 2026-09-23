"""meetings: a title schakl gave, and the hours a confirm booked

Purely additive. ``title_auto`` says the row's title was generated (the recorder left the box
empty), so the drafted minutes may replace it once; ``time_entry_ids`` lists the time entries a
confirm wrote for the colleagues at the table, so the page can say so and link them.

Revision ID: a3c9e1f7b5d2
Revises: c7e2a9b4d6f1
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a3c9e1f7b5d2"
down_revision = "c7e2a9b4d6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "title_auto", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "meetings",
        sa.Column("time_entry_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meetings", "time_entry_ids")
    op.drop_column("meetings", "title_auto")
