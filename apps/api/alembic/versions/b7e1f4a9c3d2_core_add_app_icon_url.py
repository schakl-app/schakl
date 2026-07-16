"""core_add_app_icon_url

Per-tenant installable-app icon (#198): ``org_settings.app_icon_url`` — the square raster
source the dynamic PWA manifest and the apple-touch-icon derive their size variants from.
Additive and reversible: nullable, no default needed; the previous image never reads it, so
rollback is safe.

Revision ID: b7e1f4a9c3d2
Revises: a9d4e7c2b1f8
Create Date: 2026-07-16 10:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e1f4a9c3d2'
down_revision: str | None = 'a9d4e7c2b1f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org_settings',
        sa.Column('app_icon_url', sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org_settings', 'app_icon_url')
