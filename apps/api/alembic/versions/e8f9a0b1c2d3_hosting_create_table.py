"""hosting_create_table

Revision ID: e8f9a0b1c2d3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-11 15:22:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'e8f9a0b1c2d3'
down_revision: str | None = 'e7f8a9b0c1d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('hosting',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=True),
    sa.Column('provider_id', sa.UUID(), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('contact_party_type', sa.String(length=20), nullable=True),
    sa.Column('contact_party_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('custom', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_hosting_company_id_companies'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_hosting_org_id_orgs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], name=op.f('fk_hosting_provider_id_providers'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hosting'))
    )
    op.create_index(op.f('ix_hosting_company_id'), 'hosting', ['company_id'], unique=False)
    op.create_index('ix_hosting_custom', 'hosting', ['custom'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_hosting_name'), 'hosting', ['name'], unique=False)
    op.create_index(op.f('ix_hosting_org_id'), 'hosting', ['org_id'], unique=False)

    # Tenant isolation (defence-in-depth): hosting is org-scoped, RLS-forced (CLAUDE.md §5).
    enable_rls("hosting")


def downgrade() -> None:
    disable_rls("hosting")
    op.drop_index(op.f('ix_hosting_org_id'), table_name='hosting')
    op.drop_index(op.f('ix_hosting_name'), table_name='hosting')
    op.drop_index('ix_hosting_custom', table_name='hosting', postgresql_using='gin')
    op.drop_index(op.f('ix_hosting_company_id'), table_name='hosting')
    op.drop_table('hosting')
