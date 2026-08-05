"""mollie_create_tables

Revision ID: c8e5b03fa217
Revises: f7c2a91e40b6
Create Date: 2026-08-05 10:05:00.000000

The mollie module's one table (issue #267): the tenant's Mollie API key, org-scoped and
RLS-forced (CLAUDE.md §5). Purely additive — a new module's own table, so an existing install
upgrades unattended and a rollback drops only what this created.

A **row, not a settings singleton**, for the reason ``cloudflare_accounts`` and
``oxxa_accounts`` are rows: an agency integrating holds a live key *and* a test key at the same
time, and a singleton would have made the second one an overwrite — which for a payment
credential means either taking real money in a test or failing to take any in production.

``webhook_secret`` is ``NOT NULL`` with no default on purpose: there is no correct value to
back-fill, and a row without one would answer a callback URL that authenticates nothing. The
table is new, so nothing has to be back-filled.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'c8e5b03fa217'
down_revision: str | None = 'f7c2a91e40b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'mollie_accounts',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('api_key_encrypted', sa.Text(), nullable=False),
        sa.Column('mode', sa.String(length=10), server_default='live', nullable=False),
        sa.Column('webhook_secret', sa.String(length=64), nullable=False),
        sa.Column('methods', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('provider_id', sa.UUID(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_mollie_accounts_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], name=op.f('fk_mollie_accounts_provider_id_providers'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mollie_accounts')),
        sa.UniqueConstraint('org_id', 'name', name='uq_mollie_accounts_org_name'),
    )
    op.create_index(op.f('ix_mollie_accounts_org_id'), 'mollie_accounts', ['org_id'], unique=False)
    op.create_index('ix_mollie_accounts_org_active', 'mollie_accounts', ['org_id', 'active'], unique=False)
    enable_rls('mollie_accounts')


def downgrade() -> None:
    disable_rls('mollie_accounts')
    op.drop_index('ix_mollie_accounts_org_active', table_name='mollie_accounts')
    op.drop_index(op.f('ix_mollie_accounts_org_id'), table_name='mollie_accounts')
    op.drop_table('mollie_accounts')
