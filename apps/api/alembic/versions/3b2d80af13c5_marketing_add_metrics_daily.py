"""marketing_add_metrics_daily

Revision ID: 3b2d80af13c5
Revises: 3a1c7f9e2b40
Create Date: 2026-07-13 00:00:01.000000

The stored daily aggregates (epic #134, #133): one small row per link per day, upserted
idempotently on (org_id, link_id, date). Expand-only and rollback-safe like its sibling — a new
table nothing older reads. Tier-2 drill-downs are fetched live and never stored here.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = '3b2d80af13c5'
down_revision: str | None = '3a1c7f9e2b40'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'marketing_metrics_daily',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('link_id', sa.UUID(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column(
            'metrics', postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['link_id'], ['marketing_links.id'],
            name=op.f('fk_marketing_metrics_daily_link_id_marketing_links'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_marketing_metrics_daily_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_marketing_metrics_daily')),
        sa.UniqueConstraint(
            'org_id', 'link_id', 'date', name='uq_marketing_metrics_daily_key'
        ),
    )
    op.create_index(
        op.f('ix_marketing_metrics_daily_org_id'), 'marketing_metrics_daily', ['org_id']
    )
    op.create_index(
        op.f('ix_marketing_metrics_daily_link_id'), 'marketing_metrics_daily', ['link_id']
    )
    op.create_index(
        'ix_marketing_metrics_daily_link_date', 'marketing_metrics_daily',
        ['org_id', 'link_id', 'date'],
    )

    enable_rls('marketing_metrics_daily')


def downgrade() -> None:
    disable_rls('marketing_metrics_daily')
    op.drop_index('ix_marketing_metrics_daily_link_date', table_name='marketing_metrics_daily')
    op.drop_index(
        op.f('ix_marketing_metrics_daily_link_id'), table_name='marketing_metrics_daily'
    )
    op.drop_index(
        op.f('ix_marketing_metrics_daily_org_id'), table_name='marketing_metrics_daily'
    )
    op.drop_table('marketing_metrics_daily')
