"""wordpress_add_bridge_updates

Revision ID: a3d7f2c9e5b8
Revises: e8a2c5f9d4b1
Create Date: 2026-09-25

One nullable JSONB column on ``wordpress_sites``: whether the schakl WordPress MCP Bridge plugin
can update itself, as its ``info`` last reported (bridge 1.3.1+). Purely additive — an older
release neither reads nor writes it, and NULL is what every existing row means already ("no
probe has seen a plugin that reports it").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a3d7f2c9e5b8"
down_revision: str | None = "e8a2c5f9d4b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wordpress_sites",
        sa.Column("bridge_updates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wordpress_sites", "bridge_updates")
