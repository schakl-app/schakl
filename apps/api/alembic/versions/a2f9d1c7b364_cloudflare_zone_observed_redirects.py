"""cloudflare_zone_observed_redirects

Revision ID: a2f9d1c7b364
Revises: f3b6c81a9e27
Create Date: 2026-08-11 10:20:00.000000

Two columns on ``cloudflare_zones`` so a redirect that already exists at Cloudflare survives the
page load (#278).

``GET /domains/{id}/status`` deliberately reads stored rows and calls nothing — a domain page must
not wait on an outside API — and the ruleset observation was stored nowhere at all. So the one
state this module exists to serve, an agency taking over a client's Cloudflare where the redirect
was made by hand months ago, was discoverable only by pressing "Controleren bij Cloudflare" and
was forgotten again the moment the page reloaded.

- ``observed_redirects`` — the redirects on this zone that schakl does not own, as the last
  successful check saw them. Purely observed; nothing here is ever pushed anywhere.
- ``redirects_observed_at`` — when that list was last *read*. ``NULL`` = nobody has ever looked,
  which an empty list on its own cannot say, and reading "never checked" as "no redirects" is
  exactly the wrong answer to give about a live client's zone.

Additive, defaulted and nullable, so an existing install upgrades unattended: every pre-existing
zone reads as "nothing observed yet", which is precisely what it is. The first check of each
domain fills it in.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a2f9d1c7b364'
down_revision: str | None = 'b8d4e1f60a25'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'cloudflare_zones',
        sa.Column(
            'observed_redirects',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        'cloudflare_zones',
        sa.Column('redirects_observed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('cloudflare_zones', 'redirects_observed_at')
    op.drop_column('cloudflare_zones', 'observed_redirects')
