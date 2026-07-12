"""domains_add_mx_records

Revision ID: f94b48d42520
Revises: 9acc35eb863a
Create Date: 2026-07-12 00:00:00.000000

Additive expand (#125): one nullable JSONB column for the domain's MX records,
``[{priority, exchange}]`` in priority order. NULL = never checked (like the #92 columns),
``[]`` = checked, no MX. No backfill — the daily refresh cron and the on-create fetch fill it.
Reversible; the previous release never reads the column, so rollback stays safe.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f94b48d42520'
down_revision: str | None = '9acc35eb863a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'domains',
        sa.Column('mx_records', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('domains', 'mx_records')
