"""domains_create_table

Revision ID: e6f7a8b9c0d1
Revises: a4556bc04430
Create Date: 2026-07-11 15:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: str | None = 'a4556bc04430'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The nameserver / DNSSEC columns are populated by the public-DNS refresh slice (#92); they
    # are created NULL-able here so that slice is code-only.
    op.create_table('domains',
    sa.Column('name', sa.String(length=253), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('registrar_provider_id', sa.UUID(), nullable=True),
    sa.Column('dns_provider_id', sa.UUID(), nullable=True),
    sa.Column('registry_contact_party_type', sa.String(length=20), nullable=True),
    sa.Column('registry_contact_party_id', sa.UUID(), nullable=True),
    sa.Column('email_enabled', sa.Boolean(), nullable=False),
    sa.Column('email_provider_id', sa.UUID(), nullable=True),
    sa.Column('email_contact_party_type', sa.String(length=20), nullable=True),
    sa.Column('email_contact_party_id', sa.UUID(), nullable=True),
    sa.Column('nameservers', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('dnssec', sa.Boolean(), nullable=True),
    sa.Column('dns_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('custom', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_domains_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['dns_provider_id'], ['providers.id'], name=op.f('fk_domains_dns_provider_id_providers'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['email_provider_id'], ['providers.id'], name=op.f('fk_domains_email_provider_id_providers'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_domains_org_id_orgs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['registrar_provider_id'], ['providers.id'], name=op.f('fk_domains_registrar_provider_id_providers'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_domains')),
    sa.UniqueConstraint('org_id', 'name', name='uq_domains_org_name')
    )
    op.create_index(op.f('ix_domains_company_id'), 'domains', ['company_id'], unique=False)
    op.create_index('ix_domains_custom', 'domains', ['custom'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_domains_name'), 'domains', ['name'], unique=False)
    op.create_index(op.f('ix_domains_org_id'), 'domains', ['org_id'], unique=False)
    op.create_index(op.f('ix_domains_status'), 'domains', ['status'], unique=False)

    # Tenant isolation (defence-in-depth): domains are org-scoped, RLS-forced (CLAUDE.md §5).
    enable_rls("domains")


def downgrade() -> None:
    disable_rls("domains")
    op.drop_index(op.f('ix_domains_status'), table_name='domains')
    op.drop_index(op.f('ix_domains_org_id'), table_name='domains')
    op.drop_index(op.f('ix_domains_name'), table_name='domains')
    op.drop_index('ix_domains_custom', table_name='domains', postgresql_using='gin')
    op.drop_index(op.f('ix_domains_company_id'), table_name='domains')
    op.drop_table('domains')
