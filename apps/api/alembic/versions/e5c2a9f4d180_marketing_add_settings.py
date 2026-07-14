"""marketing_add_settings

Revision ID: e5c2a9f4d180
Revises: d3f8a1c04b62
Create Date: 2026-07-14 00:10:00.000000

Org-level marketing settings (epic #134): one row per org holding the encrypted Google Ads
developer token, moving it out of instance env config (``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN``) and
into per-org settings like the Google OAuth client secret. Expand-only and rollback-safe: a new
table nothing older reads, and the env var stays a fallback, so an install that had the token in
its environment keeps working with no data migration.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'e5c2a9f4d180'
down_revision: str | None = 'd3f8a1c04b62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'marketing_settings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('ads_developer_token_encrypted', sa.Text(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_marketing_settings_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_marketing_settings')),
        sa.UniqueConstraint('org_id', name='uq_marketing_settings_org'),
    )
    op.create_index(
        op.f('ix_marketing_settings_org_id'), 'marketing_settings', ['org_id']
    )

    enable_rls('marketing_settings')


def downgrade() -> None:
    disable_rls('marketing_settings')
    op.drop_index(op.f('ix_marketing_settings_org_id'), table_name='marketing_settings')
    op.drop_table('marketing_settings')
