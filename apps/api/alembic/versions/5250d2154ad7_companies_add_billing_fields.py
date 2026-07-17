"""companies_add_billing_fields

Revision ID: 5250d2154ad7
Revises: c4e1b8a6f025
Create Date: 2026-07-16 00:00:00.000000

Billing identity on companies (issue #11, needed by invoicing #207): VAT / CoC numbers and
the postal address an invoice header or a UBL export prints. Expand-only: seven nullable
columns, no backfill, no defaults — existing rows simply have no billing data yet, which is
also what a fresh form shows. Older releases never read these columns, so rollback (drop) is
safe from any released version.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5250d2154ad7'
down_revision: str | None = 'c4e1b8a6f025'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    sa.Column('vat_number', sa.String(length=32), nullable=True),
    sa.Column('coc_number', sa.String(length=32), nullable=True),
    sa.Column('address_line1', sa.String(length=255), nullable=True),
    sa.Column('address_line2', sa.String(length=255), nullable=True),
    sa.Column('postal_code', sa.String(length=16), nullable=True),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('country', sa.String(length=2), nullable=True),
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column('companies', column)


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column('companies', column.name)
