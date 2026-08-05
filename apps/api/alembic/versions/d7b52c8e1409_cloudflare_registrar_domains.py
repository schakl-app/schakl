"""cloudflare_registrar_domains

Revision ID: d7b52c8e1409
Revises: c41d7ae90b52
Create Date: 2026-08-04 00:00:00.000000

Cloudflare Registrar as a *register* (#298): one org-scoped, RLS-forced table for what the
Registrar API says about a domain, plus ``cloudflare_accounts.registrar_synced_at``.

Its own table rather than columns on ``cloudflare_zones``, because a zone and a registration are
different facts about a name: Cloudflare runs DNS for plenty of domains a client registered and
pays for themselves, and those are exactly the ones an agency must not invoice. The list also
reports domains held at other registrars, which is what ``at_cloudflare`` (derived at sync time
from ``current_registrar``) exists to separate.

``registrar_synced_at`` sits beside ``last_synced_at`` rather than replacing it for the same
reason: syncing zones is not reading the register, and only a register that has answered may
narrow what schakl invoices. NULL — the value every existing row gets — means "never read", so
an install that upgrades into this migration keeps invoicing exactly what it did yesterday.

Purely additive; rollback drops only what this created.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'd7b52c8e1409'
down_revision: str | None = 'c41d7ae90b52'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'cloudflare_registrar_domains'


def upgrade() -> None:
    op.add_column(
        'cloudflare_accounts',
        sa.Column('registrar_synced_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        _TABLE,
        sa.Column('account_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=253), nullable=False),
        sa.Column('cf_registrar_id', sa.String(length=64), nullable=True),
        sa.Column('domain_id', sa.UUID(), nullable=True),
        sa.Column('current_registrar', sa.String(length=128), nullable=True),
        sa.Column('at_cloudflare', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('auto_renew', sa.Boolean(), nullable=True),
        sa.Column('locked', sa.Boolean(), nullable=True),
        sa.Column('registry_statuses', sa.String(length=255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['cloudflare_accounts.id'], name=op.f('fk_cloudflare_registrar_domains_account_id_cloudflare_accounts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_cloudflare_registrar_domains_domain_id_domains'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_cloudflare_registrar_domains_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cloudflare_registrar_domains')),
        sa.UniqueConstraint('org_id', 'account_id', 'name', name='uq_cloudflare_registrar_domains_org_name'),
    )
    op.create_index(op.f('ix_cloudflare_registrar_domains_account_id'), _TABLE, ['account_id'], unique=False)
    op.create_index(op.f('ix_cloudflare_registrar_domains_org_id'), _TABLE, ['org_id'], unique=False)
    op.create_index('ix_cloudflare_registrar_domains_org_name', _TABLE, ['org_id', 'name'], unique=False)
    op.create_index('ix_cloudflare_registrar_domains_org_domain', _TABLE, ['org_id', 'domain_id'], unique=False)
    enable_rls(_TABLE)


def downgrade() -> None:
    disable_rls(_TABLE)
    op.drop_table(_TABLE)
    op.drop_column('cloudflare_accounts', 'registrar_synced_at')
