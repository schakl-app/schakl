"""invoicing_add_domain_billing

The ``domain.due`` consumer (issue #250) drafts one invoice per (domain, period), exactly
like the subscription cycle: ``invoices.domain_id`` is a bare cross-module UUID (§6, no FK)
and the partial unique index is the idempotency backstop that makes a re-run, crash-resume
or double emit unable to double-bill (#31's hard rule). Additive expand: nullable column +
index only, so rollback stays safe.

Revision ID: 678320ce0bfd
Revises: 3c14443ed1fc
Create Date: 2026-07-25 12:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '678320ce0bfd'
down_revision: str | None = '3c14443ed1fc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('invoices', sa.Column('domain_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_invoices_domain_id'), 'invoices', ['domain_id'], unique=False)
    op.create_index(
        'uq_invoices_domain_period',
        'invoices',
        ['org_id', 'domain_id', 'period_end'],
        unique=True,
        postgresql_where=sa.text('domain_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_invoices_domain_period', table_name='invoices')
    op.drop_index(op.f('ix_invoices_domain_id'), table_name='invoices')
    op.drop_column('invoices', 'domain_id')
