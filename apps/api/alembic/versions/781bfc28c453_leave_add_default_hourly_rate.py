"""leave_add_default_hourly_rate

Revision ID: 781bfc28c453
Revises: 82fbc87ed4fd
Create Date: 2026-07-12 00:00:00.000000

Additive expand (#113): the org's default hourly rate on leave_settings — the fallback when an
employee has no rate of their own (#82). NULL for every existing row means "no default", which
is exactly today's behaviour. Reversible; the previous release never reads the column.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '781bfc28c453'
down_revision: str | None = '82fbc87ed4fd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_settings',
        sa.Column('default_hourly_rate', sa.Numeric(precision=10, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('leave_settings', 'default_hourly_rate')
