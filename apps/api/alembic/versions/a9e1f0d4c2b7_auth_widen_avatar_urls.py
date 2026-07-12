"""auth_widen_avatar_urls

Revision ID: a9e1f0d4c2b7
Revises: c9e6ec23e591
Create Date: 2026-07-12 00:00:00.000000

Expand (#122): the avatar URL columns become TEXT. VARCHAR(1024) looked generous until the
first real Google picture claim arrived (v0.5.0 made the userinfo endpoint actually get
called): the signed `lh3.googleusercontent.com/a-/ALV-…` form runs past 1,024 characters,
and the overflow surfaced inside the OIDC callback's commit — failing the *login* itself
with a 500. A URL has no meaningful length bound; TEXT ends the class of bug.

Widening is metadata-only in Postgres (no table rewrite) and safe unattended. The downgrade
truncates rather than fails so a rollback cannot strand the schema — a clipped avatar URL
just falls back to initials.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9e1f0d4c2b7'
down_revision: str | None = 'c9e6ec23e591'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('users', 'oidc_avatar_url', type_=sa.Text(), existing_nullable=True)
    op.alter_column('users', 'custom_avatar_url', type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    # Clip oversized values first: ALTER TYPE VARCHAR(1024) refuses rows that no longer fit,
    # and a downgrade that dies halfway is worse than a lost avatar.
    op.execute("UPDATE users SET oidc_avatar_url = left(oidc_avatar_url, 1024) WHERE length(oidc_avatar_url) > 1024")
    op.execute("UPDATE users SET custom_avatar_url = left(custom_avatar_url, 1024) WHERE length(custom_avatar_url) > 1024")
    op.alter_column('users', 'custom_avatar_url', type_=sa.String(length=1024), existing_nullable=True)
    op.alter_column('users', 'oidc_avatar_url', type_=sa.String(length=1024), existing_nullable=True)
