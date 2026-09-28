"""wordpress_add_bridge_theme

Revision ID: b4e8d1a6c3f7
Revises: ec56853d27b0
Create Date: 2026-09-28

One nullable JSONB column on ``wordpress_sites``: where theme file editing stands on the site,
as the schakl WordPress MCP Bridge plugin's ``info`` last reported (bridge 1.5.0+). Purely
additive — an older release neither reads nor writes it, and NULL is what every existing row
means already ("no probe has seen a plugin that reports it").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b4e8d1a6c3f7"
down_revision: str | None = "ec56853d27b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wordpress_sites",
        sa.Column("bridge_theme", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wordpress_sites", "bridge_theme")
