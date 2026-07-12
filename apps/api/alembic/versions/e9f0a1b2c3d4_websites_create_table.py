"""websites_create_table

Revision ID: e9f0a1b2c3d4
Revises: e8f9a0b1c2d3
Create Date: 2026-07-11 15:23:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'e9f0a1b2c3d4'
down_revision: str | None = 'e8f9a0b1c2d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('websites',
    sa.Column('domain_id', sa.UUID(), nullable=False),
    sa.Column('root', sa.Boolean(), nullable=False),
    sa.Column('technical_owner_party_type', sa.String(length=20), nullable=True),
    sa.Column('technical_owner_party_id', sa.UUID(), nullable=True),
    sa.Column('hosting_id', sa.UUID(), nullable=True),
    sa.Column('uptime_enabled', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('custom', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_websites_domain_id_domains'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['hosting_id'], ['hosting.id'], name=op.f('fk_websites_hosting_id_hosting'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_websites_org_id_orgs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_websites')),
    sa.UniqueConstraint('org_id', 'domain_id', name='uq_websites_domain')
    )
    op.create_index('ix_websites_custom', 'websites', ['custom'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_websites_domain_id'), 'websites', ['domain_id'], unique=False)
    op.create_index(op.f('ix_websites_org_id'), 'websites', ['org_id'], unique=False)

    # Tenant isolation (defence-in-depth): websites are org-scoped, RLS-forced (CLAUDE.md §5).
    enable_rls("websites")


def downgrade() -> None:
    disable_rls("websites")
    op.drop_index(op.f('ix_websites_org_id'), table_name='websites')
    op.drop_index(op.f('ix_websites_domain_id'), table_name='websites')
    op.drop_index('ix_websites_custom', table_name='websites', postgresql_using='gin')
    op.drop_table('websites')
