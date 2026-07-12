"""activity_create_log

The core activity trail (issue #67): a single polymorphic, org-scoped, RLS-forced table that
records *what changed, by whom, when* for any auditable entity. Promotes the tasks-only
``task_activities`` shape into core so companies, contacts, projects (and later everything with
a mutable record) share one paper trail instead of each reinventing or omitting it.

``entity_id`` carries **no FK** — the trail must outlive the record it describes. ``actor_name``
snapshots the actor at write time so a since-deleted account's history does not silently become
the system's (issue #64), exactly as ``task_activities`` already does.

Upgrade path: purely additive — one new table, RLS-forced like every org-scoped table. Nothing
to backfill (the trail starts empty and accrues going forward). A rolled-back image simply
ignores the table. Safe to run unattended on a populated database; a real ``downgrade`` drops it.

Revision ID: c1a2b3d4e5f6
Revises: b4c5d6e7f8a9
Create Date: 2026-07-11 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: str | None = 'b4c5d6e7f8a9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'activity_log',
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('actor_user_id', sa.UUID(), nullable=True),
        sa.Column('actor_name', sa.String(length=255), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'],
                                name=op.f('fk_activity_log_actor_user_id_users'),
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_activity_log_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_activity_log')),
    )
    op.create_index(op.f('ix_activity_log_org_id'), 'activity_log', ['org_id'], unique=False)
    op.create_index('ix_activity_log_entity', 'activity_log',
                    ['org_id', 'entity_type', 'entity_id', 'created_at'], unique=False)

    # Tenant isolation (defence-in-depth): org-scoped, RLS-forced like every domain table.
    enable_rls('activity_log')


def downgrade() -> None:
    disable_rls('activity_log')
    op.drop_index('ix_activity_log_entity', table_name='activity_log')
    op.drop_index(op.f('ix_activity_log_org_id'), table_name='activity_log')
    op.drop_table('activity_log')
