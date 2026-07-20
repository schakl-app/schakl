"""domains_add_redirect_url

Revision ID: c7d1e9f3a2b8
Revises: d4b8f2c6a1e7
Create Date: 2026-07-18 00:00:00.000000

Additive expand (owner request): one nullable ``redirect_url`` column that records where a
``redirect``-status domain should point. NULL/absent for every other status; no backfill and the
previous release never reads it, so rollback stays safe.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7d1e9f3a2b8'
down_revision: str | None = 'd4b8f2c6a1e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'domains',
        sa.Column('redirect_url', sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('domains', 'redirect_url')
