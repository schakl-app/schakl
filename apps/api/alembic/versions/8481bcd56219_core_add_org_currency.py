"""core_add_org_currency

Revision ID: 8481bcd56219
Revises: 4c4f90fc2dae
Create Date: 2026-07-12 00:00:00.000000

Additive expand (#124): org_settings.currency (ISO 4217, NOT NULL) with server_default 'EUR',
so every existing row keeps meaning exactly what it meant — money was hardcoded EUR before.
Reversible; the previous release never reads the column, so rollback stays safe.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8481bcd56219'
down_revision: str | None = '4c4f90fc2dae'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org_settings',
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='EUR'),
    )


def downgrade() -> None:
    op.drop_column('org_settings', 'currency')
