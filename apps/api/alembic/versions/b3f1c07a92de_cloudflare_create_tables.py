"""cloudflare_create_tables

Revision ID: b3f1c07a92de
Revises: a1d3f7b25c94
Create Date: 2026-08-01 09:00:00.000000

The cloudflare module's five tables (epic #278), all org-scoped and RLS-forced (CLAUDE.md §5).
Purely additive — a new module's own tables, so an existing install upgrades unattended and a
rollback drops only what this created (docs/WORKFLOW.md's expand/contract rule has nothing to
contract here).

Two constraints are load-bearing rather than incidental:

* ``uq_cloudflare_zones_org_zone`` is on ``(org_id, cf_zone_id)`` and **not** on the zone name.
  Cloudflare allows the same apex to exist in several accounts as long as only one is *active*,
  and an agency that holds both its own account and a client's genuinely hits that. A unique
  name would turn the second sync into a crash instead of a reported ambiguity.
* ``uq_cloudflare_redirects_org_zone`` is what makes "the redirect schakl owns" singular. The
  tenant may have any number of their own redirect rules on the zone; this module has one.

``cloudflare_zones.domain_id`` is ``SET NULL`` and every other FK to ``domains`` is ``CASCADE``:
deleting a domain record must forget the redirect and the Pages links it configured (they mean
nothing without it) while leaving the *zone* row, which still describes something real at
Cloudflare, visible as an unmatched zone.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'b3f1c07a92de'
down_revision: str | None = 'a1d3f7b25c94'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    'cloudflare_pages_links',
    'cloudflare_pages_projects',
    'cloudflare_redirects',
    'cloudflare_zones',
    'cloudflare_accounts',
)


def upgrade() -> None:
    op.create_table(
        'cloudflare_accounts',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('api_token_encrypted', sa.Text(), nullable=False),
        sa.Column('cf_account_id', sa.String(length=64), nullable=True),
        sa.Column('cf_account_name', sa.String(length=255), nullable=True),
        sa.Column('provider_id', sa.UUID(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
        sa.Column('capabilities', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_cloudflare_accounts_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], name=op.f('fk_cloudflare_accounts_provider_id_providers'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cloudflare_accounts')),
        sa.UniqueConstraint('org_id', 'name', name='uq_cloudflare_accounts_org_name'),
    )
    op.create_index(op.f('ix_cloudflare_accounts_org_id'), 'cloudflare_accounts', ['org_id'], unique=False)
    op.create_index('ix_cloudflare_accounts_org_active', 'cloudflare_accounts', ['org_id', 'active'], unique=False)

    op.create_table(
        'cloudflare_zones',
        sa.Column('account_id', sa.UUID(), nullable=False),
        sa.Column('cf_zone_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=253), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('plan', sa.String(length=64), nullable=True),
        sa.Column('paused', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('name_servers', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('original_name_servers', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('domain_id', sa.UUID(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['cloudflare_accounts.id'], name=op.f('fk_cloudflare_zones_account_id_cloudflare_accounts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_cloudflare_zones_domain_id_domains'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_cloudflare_zones_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cloudflare_zones')),
        sa.UniqueConstraint('org_id', 'cf_zone_id', name='uq_cloudflare_zones_org_zone'),
    )
    op.create_index(op.f('ix_cloudflare_zones_account_id'), 'cloudflare_zones', ['account_id'], unique=False)
    op.create_index(op.f('ix_cloudflare_zones_org_id'), 'cloudflare_zones', ['org_id'], unique=False)
    op.create_index('ix_cloudflare_zones_org_name', 'cloudflare_zones', ['org_id', 'name'], unique=False)
    op.create_index('ix_cloudflare_zones_org_domain', 'cloudflare_zones', ['org_id', 'domain_id'], unique=False)

    op.create_table(
        'cloudflare_redirects',
        sa.Column('zone_id', sa.UUID(), nullable=False),
        sa.Column('domain_id', sa.UUID(), nullable=False),
        sa.Column('target_url', sa.String(length=2048), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('preserve_path', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('preserve_query', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('include_subdomains', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('cf_ruleset_id', sa.String(length=64), nullable=True),
        sa.Column('cf_rule_id', sa.String(length=64), nullable=True),
        sa.Column('last_status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_pushed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_cloudflare_redirects_domain_id_domains'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_cloudflare_redirects_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['zone_id'], ['cloudflare_zones.id'], name=op.f('fk_cloudflare_redirects_zone_id_cloudflare_zones'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cloudflare_redirects')),
        sa.UniqueConstraint('org_id', 'zone_id', name='uq_cloudflare_redirects_org_zone'),
    )
    op.create_index(op.f('ix_cloudflare_redirects_domain_id'), 'cloudflare_redirects', ['domain_id'], unique=False)
    op.create_index(op.f('ix_cloudflare_redirects_org_id'), 'cloudflare_redirects', ['org_id'], unique=False)
    op.create_index(op.f('ix_cloudflare_redirects_zone_id'), 'cloudflare_redirects', ['zone_id'], unique=False)
    op.create_index('ix_cloudflare_redirects_org_domain', 'cloudflare_redirects', ['org_id', 'domain_id'], unique=False)

    op.create_table(
        'cloudflare_pages_projects',
        sa.Column('account_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('subdomain', sa.String(length=253), nullable=True),
        sa.Column('production_branch', sa.String(length=255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['cloudflare_accounts.id'], name=op.f('fk_cloudflare_pages_projects_account_id_cloudflare_accounts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_cloudflare_pages_projects_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cloudflare_pages_projects')),
        sa.UniqueConstraint('org_id', 'account_id', 'name', name='uq_cloudflare_pages_org_name'),
    )
    op.create_index(op.f('ix_cloudflare_pages_projects_account_id'), 'cloudflare_pages_projects', ['account_id'], unique=False)
    op.create_index(op.f('ix_cloudflare_pages_projects_org_id'), 'cloudflare_pages_projects', ['org_id'], unique=False)

    op.create_table(
        'cloudflare_pages_links',
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('domain_id', sa.UUID(), nullable=False),
        sa.Column('hostname', sa.String(length=253), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_cloudflare_pages_links_domain_id_domains'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_cloudflare_pages_links_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['cloudflare_pages_projects.id'], name=op.f('fk_cloudflare_pages_links_project_id_cloudflare_pages_projects'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cloudflare_pages_links')),
        sa.UniqueConstraint('org_id', 'project_id', 'hostname', name='uq_cloudflare_pages_links_host'),
    )
    op.create_index(op.f('ix_cloudflare_pages_links_domain_id'), 'cloudflare_pages_links', ['domain_id'], unique=False)
    op.create_index(op.f('ix_cloudflare_pages_links_org_id'), 'cloudflare_pages_links', ['org_id'], unique=False)
    op.create_index(op.f('ix_cloudflare_pages_links_project_id'), 'cloudflare_pages_links', ['project_id'], unique=False)
    op.create_index('ix_cloudflare_pages_links_org_domain', 'cloudflare_pages_links', ['org_id', 'domain_id'], unique=False)

    for name in reversed(_TABLES):
        enable_rls(name)


def downgrade() -> None:
    for name in _TABLES:
        disable_rls(name)
    for name in _TABLES:
        op.drop_table(name)
