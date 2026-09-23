"""wordpress_add_bridge_version

Revision ID: c7e2a9b4d6f1
Revises: f1b6d3a8c2e4
Create Date: 2026-09-23

One nullable column on ``wordpress_sites``: the breik. Bridge plugin's version as the last
probe observed it. Purely additive — an older release neither reads nor writes it, and NULL is
what every existing row means already ("no probe has seen the plugin").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7e2a9b4d6f1"
down_revision: str | None = "f1b6d3a8c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wordpress_sites", sa.Column("bridge_version", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("wordpress_sites", "bridge_version")
