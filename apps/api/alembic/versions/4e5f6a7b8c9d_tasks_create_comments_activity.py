"""tasks_create_comments_activity

Comments and the append-only activity log for tasks. Org-scoped + RLS-forced. Author/actor
are SET NULL so history survives a user's removal.

Revision ID: 4e5f6a7b8c9d
Revises: 3d4e5f6a7b8c
Create Date: 2026-07-07 10:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = '4e5f6a7b8c9d'
down_revision: str | None = '3d4e5f6a7b8c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_comments',
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('author_user_id', sa.UUID(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['author_user_id'], ['users.id'], name=op.f('fk_task_comments_author_user_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_comments_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_comments_task_id_tasks'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_comments')),
    )
    op.create_index(op.f('ix_task_comments_org_id'), 'task_comments', ['org_id'], unique=False)
    op.create_index(op.f('ix_task_comments_task_id'), 'task_comments', ['task_id'], unique=False)

    op.create_table(
        'task_activities',
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('actor_user_id', sa.UUID(), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_task_activities_actor_user_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_activities_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_activities_task_id_tasks'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_activities')),
    )
    op.create_index(op.f('ix_task_activities_org_id'), 'task_activities', ['org_id'], unique=False)
    op.create_index('ix_task_activities_task_id_created_at', 'task_activities', ['task_id', 'created_at'], unique=False)

    enable_rls("task_comments")
    enable_rls("task_activities")


def downgrade() -> None:
    disable_rls("task_activities")
    disable_rls("task_comments")

    op.drop_index('ix_task_activities_task_id_created_at', table_name='task_activities')
    op.drop_index(op.f('ix_task_activities_org_id'), table_name='task_activities')
    op.drop_table('task_activities')
    op.drop_index(op.f('ix_task_comments_task_id'), table_name='task_comments')
    op.drop_index(op.f('ix_task_comments_org_id'), table_name='task_comments')
    op.drop_table('task_comments')
