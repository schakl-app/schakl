"""marketing_add_links

Revision ID: 3a1c7f9e2b40
Revises: 7676a29a560e
Create Date: 2026-07-13 00:00:00.000000

New module table (epic #134, #132): a company's links to its GA4 / Search Console / Ads
properties. Expand-only: additive DDL, no backfill, nothing else references it, and older code
never reads it — rollback (downgrade drops the table + its RLS policy) is safe from any released
version. The link carries its own sync-health columns (``last_synced_at`` / ``last_error`` /
``backfill_done``) so the sync in #133 (the sibling migration) only adds the metrics table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = '3a1c7f9e2b40'
down_revision: str | None = '7676a29a560e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'marketing_links',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('source', sa.String(length=8), nullable=False),
        sa.Column('external_id', sa.String(length=512), nullable=False),
        sa.Column('display_name', sa.String(length=512), nullable=False),
        sa.Column('connection_id', sa.UUID(), nullable=True),
        sa.Column(
            'config', postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column(
            'backfill_done', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'],
            name=op.f('fk_marketing_links_company_id_companies'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['connection_id'], ['google_connections.id'],
            name=op.f('fk_marketing_links_connection_id_google_connections'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'], ['users.id'],
            name=op.f('fk_marketing_links_created_by_user_id_users'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_marketing_links_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_marketing_links')),
    )
    op.create_index(op.f('ix_marketing_links_org_id'), 'marketing_links', ['org_id'])
    op.create_index(op.f('ix_marketing_links_company_id'), 'marketing_links', ['company_id'])
    op.create_index(
        op.f('ix_marketing_links_connection_id'), 'marketing_links', ['connection_id']
    )
    op.create_index('ix_marketing_links_org_company', 'marketing_links', ['org_id', 'company_id'])
    op.create_index(
        'ix_marketing_links_org_source_active', 'marketing_links',
        ['org_id', 'source', 'active'],
    )

    enable_rls('marketing_links')


def downgrade() -> None:
    disable_rls('marketing_links')
    op.drop_index('ix_marketing_links_org_source_active', table_name='marketing_links')
    op.drop_index('ix_marketing_links_org_company', table_name='marketing_links')
    op.drop_index(op.f('ix_marketing_links_connection_id'), table_name='marketing_links')
    op.drop_index(op.f('ix_marketing_links_company_id'), table_name='marketing_links')
    op.drop_index(op.f('ix_marketing_links_org_id'), table_name='marketing_links')
    op.drop_table('marketing_links')
