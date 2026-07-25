"""domains_add_pricing

TLD-based pricing + yearly renewal cadence for domains (issue #250).

``domains`` gains ``start_date`` (NOT NULL, backfilled from ``created_at`` — the best
available proxy for existing rows), ``tld`` (stamped, backfilled from ``name`` so existing
rows resolve a price without a re-save), ``price_override`` and ``next_invoice_date``
(backfilled to the first anniversary of ``start_date`` still ahead, so onboarding an
existing book starts billing at the *next* renewal and never back-bills history).
``domain_tld_prices`` is the append-only per-TLD price history (``SubscriptionPrice``'s
shape).

RLS is FORCED on ``domains``, so the backfills bind the GUC per org (the
``9d0e1f2a3b4c`` mechanism); each statement is idempotent (``WHERE … IS NULL``).

Revision ID: 3c14443ed1fc
Revises: c264ad70e1f9
Create Date: 2026-07-25 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = '3c14443ed1fc'
down_revision: str | None = 'c264ad70e1f9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Statuses that renew and therefore bill (#250); mirrors ``models.BILLABLE_STATUSES``
#: as literals — a migration must apply on top of any older head and never import the
#: evolving application code.
_BILLABLE = ("active", "redirect", "parked")


def upgrade() -> None:
    op.add_column('domains', sa.Column('start_date', sa.Date(), nullable=True))
    op.add_column('domains', sa.Column('tld', sa.String(length=128), nullable=True))
    op.add_column('domains', sa.Column('price_override', sa.Numeric(12, 2), nullable=True))
    op.add_column('domains', sa.Column('next_invoice_date', sa.Date(), nullable=True))
    op.create_index(op.f('ix_domains_tld'), 'domains', ['tld'], unique=False)
    op.create_index(
        op.f('ix_domains_next_invoice_date'), 'domains', ['next_invoice_date'], unique=False
    )

    bind = op.get_bind()
    # All orgs, deliberately unfiltered on status: a suspended org's rows must also satisfy
    # the NOT NULL below or the upgrade aborts and the API never starts (docs/WORKFLOW.md).
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE domains
                SET start_date = (created_at AT TIME ZONE 'UTC')::date
                WHERE start_date IS NULL AND org_id = :org_id
                """
            ),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE domains
                SET tld = substr(name, strpos(name, '.') + 1)
                WHERE tld IS NULL AND strpos(name, '.') > 0 AND org_id = :org_id
                """
            ),
            {"org_id": str(org_id)},
        )
        # First anniversary of start_date strictly after today: age() counts the whole
        # years already passed, so passed + 1 is the next one ahead (clamped by Postgres
        # for 29 Feb starts, the same clamping the app's add_months does).
        bind.execute(
            sa.text(
                """
                UPDATE domains
                SET next_invoice_date = (
                    start_date + make_interval(
                        years => EXTRACT(YEAR FROM age(CURRENT_DATE, start_date))::int + 1
                    )
                )::date
                WHERE next_invoice_date IS NULL
                  AND status IN :statuses
                  AND org_id = :org_id
                """
            ).bindparams(sa.bindparam("statuses", expanding=True)),
            {"org_id": str(org_id), "statuses": list(_BILLABLE)},
        )

    op.alter_column('domains', 'start_date', nullable=False)

    op.create_table('domain_tld_prices',
    sa.Column('tld', sa.String(length=128), nullable=False),
    sa.Column('amount', sa.Numeric(12, 2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_domain_tld_prices_org_id_orgs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_domain_tld_prices')),
    sa.UniqueConstraint('org_id', 'tld', 'valid_from', name='uq_domain_tld_prices_from')
    )
    op.create_index(op.f('ix_domain_tld_prices_org_id'), 'domain_tld_prices', ['org_id'], unique=False)
    op.create_index(op.f('ix_domain_tld_prices_tld'), 'domain_tld_prices', ['tld'], unique=False)

    # Tenant isolation (defence-in-depth): org-scoped, RLS-forced (CLAUDE.md §5).
    enable_rls("domain_tld_prices")


def downgrade() -> None:
    disable_rls("domain_tld_prices")
    op.drop_index(op.f('ix_domain_tld_prices_tld'), table_name='domain_tld_prices')
    op.drop_index(op.f('ix_domain_tld_prices_org_id'), table_name='domain_tld_prices')
    op.drop_table('domain_tld_prices')
    op.drop_index(op.f('ix_domains_next_invoice_date'), table_name='domains')
    op.drop_index(op.f('ix_domains_tld'), table_name='domains')
    op.drop_column('domains', 'next_invoice_date')
    op.drop_column('domains', 'price_override')
    op.drop_column('domains', 'tld')
    op.drop_column('domains', 'start_date')
