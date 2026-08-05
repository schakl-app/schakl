"""cloudflare_pages_links_observed

Revision ID: d1a7c4be9f30
Revises: c8e5b03fa217
Create Date: 2026-08-06 09:10:00.000000

Two nullable columns on ``cloudflare_pages_links`` so the Pages half can finally say what
every other half of this module already says (CLAUDE.md §10): **what schakl decided** and
**what it last observed**, separately.

Until now the table had one writer — the "Aan project koppelen" button — and its ``status``
was frozen at whatever Cloudflare answered in the second the link was made. A hostname that
went ``pending → active`` still read "pending" forever, and one deleted in Cloudflare's own
dashboard still read as linked, because nothing ever looked again.

- ``missing_at`` — when a check last found that Cloudflare does *not* know this hostname on
  this project. ``NULL`` means present at the last look. A timestamp rather than a boolean
  because "since when" is the question an agency actually asks, and because drift is
  **reported, never acted on**: the row stays, exactly as a drifted redirect rule does.
- ``discovered_at`` — when a sync adopted this link from Cloudflare rather than the button
  creating it. It is the honest answer to "who decided this?", and it is why adoption is
  safe: recording what is already true at Cloudflare writes nothing there.

Purely additive and nullable, so an existing install upgrades unattended: every pre-existing
row reads as "created here, never yet observed missing", which is precisely what it is.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd1a7c4be9f30'
down_revision: str | None = 'c8e5b03fa217'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'cloudflare_pages_links',
        sa.Column('missing_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'cloudflare_pages_links',
        sa.Column('discovered_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('cloudflare_pages_links', 'discovered_at')
    op.drop_column('cloudflare_pages_links', 'missing_at')
