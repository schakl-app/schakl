"""companies_add_invoice_email

Adds ``invoice_email`` (factuur-emailadres) to companies — invoices routinely go to a
different mailbox (`facturen@`, `administratie@`, an accounting portal) than the day-to-day
contact person. Nullable: not every client has one yet. Additive DDL only, no backfill
needed, so no RLS FORCE dance (docs/WORKFLOW.md).

Revision ID: d4e5f6a7b8c9
Revises: c8d9e0f1a2b3
Create Date: 2026-07-09 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: str | None = 'c8d9e0f1a2b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'companies',
        sa.Column('invoice_email', sa.String(length=320), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('companies', 'invoice_email')
