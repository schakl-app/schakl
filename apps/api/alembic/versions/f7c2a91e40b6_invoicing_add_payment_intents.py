"""invoicing_add_payment_intents

Revision ID: f7c2a91e40b6
Revises: b2d8f04a71c3
Create Date: 2026-08-05 10:00:00.000000

The provider-independent half of online payments (epic #269, issue #267): one table for a
payment *attempt*, and one nullable column linking a ledger row back to the attempt that wrote
it. Org-scoped and RLS-forced like every domain table (CLAUDE.md §5).

Purely **additive** — a new table plus a nullable column and a partial index — so an existing
install upgrades unattended and a rollback drops only what this created. docs/WORKFLOW.md's
expand/contract rule has nothing to contract here.

Two constraints are the design, not bookkeeping:

* ``uq_invoice_payment_intents_external`` on ``(org_id, provider, external_id)`` is what a
  callback resolves by. One local row per provider payment, per tenant — which is why a
  forged callback naming another tenant's payment id finds nothing rather than someone else's
  invoice.
* ``uq_invoice_payments_intent`` on ``(org_id, intent_id)``, partial on ``intent_id IS NOT
  NULL``, is the idempotency of the whole path. A provider retries a webhook until it gets a
  200 (Mollie: ten times over 26 hours) and two deliveries can be in flight at once; an
  application-level "have we settled this?" check loses that race and a unique index does not.
  Partial because a hand-registered bank transfer has no intent, and a hundred of those must
  not contend over NULL.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'f7c2a91e40b6'
down_revision: str | None = 'b2d8f04a71c3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'invoice_payment_intents',
        sa.Column('invoice_id', sa.UUID(), nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('account_id', sa.UUID(), nullable=True),
        sa.Column('external_id', sa.String(length=160), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='open', nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('mode', sa.String(length=10), server_default='live', nullable=False),
        sa.Column('checkout_url', sa.String(length=1024), nullable=True),
        sa.Column('method', sa.String(length=40), nullable=True),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], name=op.f('fk_invoice_payment_intents_invoice_id_invoices'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_invoice_payment_intents_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_invoice_payment_intents')),
        sa.UniqueConstraint('org_id', 'provider', 'external_id', name='uq_invoice_payment_intents_external'),
    )
    op.create_index(op.f('ix_invoice_payment_intents_org_id'), 'invoice_payment_intents', ['org_id'], unique=False)
    op.create_index(op.f('ix_invoice_payment_intents_invoice_id'), 'invoice_payment_intents', ['invoice_id'], unique=False)
    op.create_index('ix_invoice_payment_intents_invoice', 'invoice_payment_intents', ['org_id', 'invoice_id'], unique=False)
    op.create_index('ix_invoice_payment_intents_open', 'invoice_payment_intents', ['org_id', 'status'], unique=False)
    enable_rls('invoice_payment_intents')

    op.add_column('invoice_payments', sa.Column('intent_id', sa.UUID(), nullable=True))
    op.create_index(
        'uq_invoice_payments_intent',
        'invoice_payments',
        ['org_id', 'intent_id'],
        unique=True,
        postgresql_where=sa.text('intent_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_invoice_payments_intent', table_name='invoice_payments')
    op.drop_column('invoice_payments', 'intent_id')

    disable_rls('invoice_payment_intents')
    op.drop_index('ix_invoice_payment_intents_open', table_name='invoice_payment_intents')
    op.drop_index('ix_invoice_payment_intents_invoice', table_name='invoice_payment_intents')
    op.drop_index(op.f('ix_invoice_payment_intents_invoice_id'), table_name='invoice_payment_intents')
    op.drop_index(op.f('ix_invoice_payment_intents_org_id'), table_name='invoice_payment_intents')
    op.drop_table('invoice_payment_intents')
