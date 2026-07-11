"""tasks_create_statuses

Tenant-configurable task status vocabulary (issue #62). ``Task.status`` was a closed
``open``/``in_progress``/``done`` enum; it becomes a ``key`` into the per-org ``task_statuses``
table, so board grouping, sort order and "is this finished?" all read from a list the tenant
manages instead of a hardcoded three-tuple.

Org-scoped and RLS-forced like every domain table (CLAUDE.md §5). The default vocabulary is seeded
lazily on first read (``app.modules.tasks.statuses.ensure_statuses``) rather than here, so a fresh
org from the first-run wizard and every existing org both get it without a data migration — and,
crucially, the seeded keys are exactly the old enum values (``open``/``in_progress``/``done``), so
tasks already holding those strings map straight onto their seeded status with nothing to backfill.

Also widens ``tasks.status`` from ``VARCHAR(20)`` to ``VARCHAR(50)`` to fit a custom slug.

Revision ID: d1e2f3a4b5c6
Revises: c6d7e8f9a0b1
Create Date: 2026-07-11 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: str | None = 'c6d7e8f9a0b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_statuses',
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('color', sa.String(length=20), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_terminal', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_statuses_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_statuses')),
        sa.UniqueConstraint('org_id', 'key', name=op.f('uq_task_statuses_org_id')),
    )
    op.create_index(op.f('ix_task_statuses_org_id'), 'task_statuses', ['org_id'], unique=False)
    enable_rls("task_statuses")

    # A configured status key can be a custom slug up to 50 chars.
    op.alter_column(
        'tasks', 'status',
        existing_type=sa.String(length=20),
        type_=sa.String(length=50),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'tasks', 'status',
        existing_type=sa.String(length=50),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
    disable_rls("task_statuses")
    op.drop_index(op.f('ix_task_statuses_org_id'), table_name='task_statuses')
    op.drop_table('task_statuses')
