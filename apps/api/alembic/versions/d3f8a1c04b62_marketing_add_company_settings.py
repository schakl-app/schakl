"""marketing_add_company_settings

Revision ID: d3f8a1c04b62
Revises: 3b2d80af13c5
Create Date: 2026-07-14 00:00:00.000000

Per-client marketing display preferences (epic #134): one row per company, carrying today a
single knob — whether GA4 key events / conversions are shown for that client. Expand-only and
rollback-safe like its siblings: a brand-new table nothing older reads, and its absence means
the pre-existing default (key events visible), so no backfill is needed.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'd3f8a1c04b62'
down_revision: str | None = '3b2d80af13c5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'marketing_company_settings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column(
            'show_key_events', sa.Boolean(), server_default=sa.text('true'), nullable=False
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
            name=op.f('fk_marketing_company_settings_company_id_companies'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_marketing_company_settings_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_marketing_company_settings')),
        sa.UniqueConstraint(
            'org_id', 'company_id', name='uq_marketing_company_settings_company'
        ),
    )
    op.create_index(
        op.f('ix_marketing_company_settings_org_id'), 'marketing_company_settings', ['org_id']
    )
    op.create_index(
        op.f('ix_marketing_company_settings_company_id'), 'marketing_company_settings',
        ['company_id'],
    )

    enable_rls('marketing_company_settings')


def downgrade() -> None:
    disable_rls('marketing_company_settings')
    op.drop_index(
        op.f('ix_marketing_company_settings_company_id'),
        table_name='marketing_company_settings',
    )
    op.drop_index(
        op.f('ix_marketing_company_settings_org_id'), table_name='marketing_company_settings'
    )
    op.drop_table('marketing_company_settings')
