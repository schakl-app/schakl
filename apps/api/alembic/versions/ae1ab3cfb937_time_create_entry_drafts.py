"""time_create_entry_drafts

Revision ID: ae1ab3cfb937
Revises: 781bfc28c453
Create Date: 2026-07-12 00:00:00.000000

New table for autosaved in-progress registrations (#44) — deliberately separate from
time_entries so nothing that computes hours can ever see a draft. Expand-only: additive DDL,
no backfill, nothing else references it, and older code never reads it, so rollback (downgrade
drops the table + its RLS policy) is safe from any released version.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'ae1ab3cfb937'
down_revision: str | None = '781bfc28c453'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'time_entry_drafts',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'],
            name=op.f('fk_time_entry_drafts_org_id_orgs'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_time_entry_drafts_user_id_users'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_time_entry_drafts')),
        sa.UniqueConstraint('org_id', 'user_id', 'entry_date', name='uq_time_entry_drafts_day'),
    )
    op.create_index(
        op.f('ix_time_entry_drafts_org_id'), 'time_entry_drafts', ['org_id'], unique=False
    )
    op.create_index(
        op.f('ix_time_entry_drafts_user_id'), 'time_entry_drafts', ['user_id'], unique=False
    )
    op.create_index(
        op.f('ix_time_entry_drafts_entry_date'), 'time_entry_drafts', ['entry_date'],
        unique=False,
    )
    enable_rls('time_entry_drafts')


def downgrade() -> None:
    disable_rls('time_entry_drafts')
    op.drop_index(op.f('ix_time_entry_drafts_entry_date'), table_name='time_entry_drafts')
    op.drop_index(op.f('ix_time_entry_drafts_user_id'), table_name='time_entry_drafts')
    op.drop_index(op.f('ix_time_entry_drafts_org_id'), table_name='time_entry_drafts')
    op.drop_table('time_entry_drafts')
