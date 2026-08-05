"""oxxa_create_tables

Revision ID: d1a7f3b60c92
Revises: c4e8a1b2d7f3
Create Date: 2026-08-04 10:00:00.000000

The oxxa module's two tables (issue #296), both org-scoped and RLS-forced (CLAUDE.md §5).
Purely additive — a new module's own tables — so an existing install upgrades unattended, and a
rollback drops only what this created. docs/WORKFLOW.md's expand/contract rule has nothing to
contract here: no existing column is touched, retyped or renamed, and the previous image runs
unchanged against this schema because nothing outside this module reads either table.

Three column choices are load-bearing rather than incidental:

* ``oxxa_domains.domain_id`` is nullable and ``SET NULL``. A row in the register that matches no
  schakl domain is the most valuable thing a sync surfaces — a domain the agency is paying to
  renew and quite possibly not billing for — so it must be storable, and deleting the domain
  record must not delete the evidence that the registration still exists.
* ``ns_desired`` and ``ns_observed`` are separate JSONB columns, never one. They are what we
  asked the registrar for and what the registrar says it has; a single column would silently
  overwrite one with the other on the next sync and make drift inexpressible (CLAUDE.md §10).
* ``dnssec``/``transfer_lock``/``autorenew`` are **nullable** booleans. NULL means "the registrar
  did not report it" — ``domain_list`` never carries DNSSEC — which is not the same as ``false``.
  A NOT NULL default here would tell an agency their DNSSEC is off when nobody has looked.

``uq_oxxa_domains_org_name`` is on ``(org_id, account_id, name)``: the same domain name can
legitimately appear in two of the tenant's reseller accounts (one mid-transfer, say), and a
unique-per-org name would turn that into a failed sync instead of two visible rows.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'd1a7f3b60c92'
down_revision: str | None = 'c4e8a1b2d7f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    'oxxa_domains',
    'oxxa_accounts',
)


def upgrade() -> None:
    op.create_table(
        'oxxa_accounts',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('api_user', sa.String(length=255), nullable=False),
        sa.Column('api_password_encrypted', sa.Text(), nullable=False),
        sa.Column('provider_id', sa.UUID(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
        sa.Column('tld_suffixes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('funds_available', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_oxxa_accounts_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], name=op.f('fk_oxxa_accounts_provider_id_providers'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_oxxa_accounts')),
        sa.UniqueConstraint('org_id', 'name', name='uq_oxxa_accounts_org_name'),
    )
    op.create_index('ix_oxxa_accounts_org_active', 'oxxa_accounts', ['org_id', 'active'], unique=False)
    op.create_index(op.f('ix_oxxa_accounts_org_id'), 'oxxa_accounts', ['org_id'], unique=False)

    op.create_table(
        'oxxa_domains',
        sa.Column('account_id', sa.UUID(), nullable=False),
        sa.Column('domain_id', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=253), nullable=False),
        sa.Column('sld', sa.String(length=63), nullable=False),
        sa.Column('tld', sa.String(length=128), nullable=False),
        sa.Column('expires_on', sa.Date(), nullable=True),
        sa.Column('transfer_lock', sa.Boolean(), nullable=True),
        sa.Column('autorenew', sa.Boolean(), nullable=True),
        sa.Column('dnssec', sa.Boolean(), nullable=True),
        sa.Column('ns_observed', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('nsgroup_ref', sa.String(length=64), nullable=True),
        sa.Column('contact_refs', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('registrant', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('registry_status', sa.String(length=32), nullable=True),
        sa.Column('ns_desired', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('ns_push_status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('ns_pushed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['oxxa_accounts.id'], name=op.f('fk_oxxa_domains_account_id_oxxa_accounts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_oxxa_domains_domain_id_domains'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_oxxa_domains_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_oxxa_domains')),
        sa.UniqueConstraint('org_id', 'account_id', 'name', name='uq_oxxa_domains_org_name'),
    )
    op.create_index('ix_oxxa_domains_org_domain', 'oxxa_domains', ['org_id', 'domain_id'], unique=False)
    op.create_index('ix_oxxa_domains_org_expires', 'oxxa_domains', ['org_id', 'expires_on'], unique=False)
    op.create_index(op.f('ix_oxxa_domains_account_id'), 'oxxa_domains', ['account_id'], unique=False)
    op.create_index(op.f('ix_oxxa_domains_org_id'), 'oxxa_domains', ['org_id'], unique=False)

    # Reversed so a child table's policy is in place before its parent's (harmless here, but it
    # keeps the pairing with ``downgrade`` obvious).
    for name in reversed(_TABLES):
        enable_rls(name)


def downgrade() -> None:
    for name in _TABLES:
        disable_rls(name)
    for name in _TABLES:
        op.drop_table(name)
