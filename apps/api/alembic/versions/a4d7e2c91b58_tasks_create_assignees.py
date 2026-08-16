"""tasks_create_assignees

A task had exactly one assignee, while the client above it and the project beside it each held a
roster (``company_assignees`` / ``project_assignees``, revision e5f6a7b8c9d0). Work shared between
two colleagues therefore had to pick a name, and "mijn taken" hid it from the other one. This adds
``task_assignees`` in the same shape: ``(org_id, task_id, user_id, is_primary)`` with a partial
unique index making at most one primary possible.

**Expand only** (docs/WORKFLOW.md). ``tasks.assignee_user_id`` stays and keeps mirroring the
primary, so:

  * every existing release upgrades into this — the column it reads is still there and correct;
  * rolling the image tag back leaves old code on this schema, and it still works;
  * the contract migration (dropping the column) ships once no released reader is left.

That column has more readers than the companies one did — reminders, scheduling, recurrence,
templates, impex, bulk and automation — which is the argument *for* mirroring rather than against
it: none of them has to learn about rosters in the release that introduces them.

Every existing ``assignee_user_id`` is backfilled as that task's primary assignee. The backfill is
idempotent (``ON CONFLICT DO NOTHING``) and set-based, so re-running it on a populated database is
a no-op rather than a duplicate-key abort.

Revision ID: a4d7e2c91b58
Revises: c5d81b3f7a26
Create Date: 2026-08-15 21:40:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'a4d7e2c91b58'
down_revision: str | None = 'c5d81b3f7a26'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_assignees',
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_assignees_task_id_tasks'), ondelete='CASCADE'),
        # A removed member loses their assignments; the task itself is never orphaned
        # (its mirrored assignee_user_id is SET NULL).
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_task_assignees_user_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_assignees_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_assignees')),
        sa.UniqueConstraint('org_id', 'task_id', 'user_id', name='uq_task_assignees_link'),
    )
    op.create_index(op.f('ix_task_assignees_task_id'), 'task_assignees', ['task_id'], unique=False)
    op.create_index(op.f('ix_task_assignees_user_id'), 'task_assignees', ['user_id'], unique=False)
    op.create_index(op.f('ix_task_assignees_org_id'), 'task_assignees', ['org_id'], unique=False)
    # At most one primary assignee per task (partial unique index).
    op.create_index(
        'uq_task_assignees_primary',
        'task_assignees',
        ['org_id', 'task_id'],
        unique=True,
        postgresql_where=sa.text('is_primary'),
    )
    # "My tasks" matches *any* assignee, so the filter's subquery is keyed by user and answers
    # with task ids: (org_id, user_id) is the prefix it reads, not (org_id, task_id).
    op.create_index(
        'ix_task_assignees_org_user',
        'task_assignees',
        ['org_id', 'user_id'],
        unique=False,
    )

    # Backfill: today's single assignee becomes the primary.
    #
    # Migrations run as the table owner under FORCE ROW LEVEL SECURITY with no tenant GUC set, so
    # an unqualified read of ``tasks`` returns zero rows (RLS fails closed) and every existing
    # assignee would be silently lost. Exempt the owner for the copy, then restore FORCE.
    op.execute("ALTER TABLE tasks NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        INSERT INTO task_assignees (id, org_id, task_id, user_id, is_primary, created_at, updated_at)
        SELECT gen_random_uuid(), t.org_id, t.id, t.assignee_user_id, true, now(), now()
        FROM tasks t
        WHERE t.assignee_user_id IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_task_assignees_link DO NOTHING
        """
    )
    op.execute("ALTER TABLE tasks FORCE ROW LEVEL SECURITY")

    # Tenant isolation (defence-in-depth): links are org-scoped, RLS-forced (CLAUDE.md §5).
    enable_rls('task_assignees')


def downgrade() -> None:
    # ``assignee_user_id`` was never dropped and has been kept in step with the primary on every
    # write, so there is nothing to restore — the links simply go away.
    disable_rls('task_assignees')
    op.drop_index('ix_task_assignees_org_user', table_name='task_assignees')
    op.drop_index('uq_task_assignees_primary', table_name='task_assignees')
    op.drop_index(op.f('ix_task_assignees_org_id'), table_name='task_assignees')
    op.drop_index(op.f('ix_task_assignees_user_id'), table_name='task_assignees')
    op.drop_index(op.f('ix_task_assignees_task_id'), table_name='task_assignees')
    op.drop_table('task_assignees')
