"""uptime_add_link_candidates

What the last match attempt found for a monitor, and when it ran (#321). Two columns rather
than one, for `cloudflare`'s reason: an empty list cannot tell "we looked and found nothing"
apart from "nobody has ever looked", and the reconciliation screen has to say which.

Additive and backfill-free. Every existing monitor starts at "never looked" (`NULL`), which is
the honest reading of a row written before matching existed — and the first sync of that
instance fills it in.

Revision ID: b8d4e1f60a25
Revises: f1c48d20a973
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b8d4e1f60a25"
down_revision = "f1c48d20a973"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "uptime_monitors",
        sa.Column(
            "link_candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "uptime_monitors",
        sa.Column("link_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("uptime_monitors", "link_checked_at")
    op.drop_column("uptime_monitors", "link_candidates")
