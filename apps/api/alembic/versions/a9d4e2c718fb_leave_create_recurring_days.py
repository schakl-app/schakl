"""leave_create_recurring_days

Recurring rostered free days / ADV patterns (#107): ``leave_recurring_days`` holds the
tenant-defined cadence ("every 2nd Friday from <date>"), and ``leave_requests`` learns which
generated occurrence a row satisfies (``recurring_day_id`` + ``recurring_date``) so a day the
employee moved or cancelled is never silently regenerated onto its pattern date.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive.** One new table plus two nullable columns (no backfill — every existing
  request is simply not pattern-generated). Applies on top of any released ``head``.
* **Rollback-safe.** The previous image never selects the new table or columns; the FK from
  ``leave_requests`` is ``ON DELETE SET NULL``, so dropping a pattern never touches requests.
* RLS is enabled on the new table like every domain table (Golden Rule 1).

Revision ID: a9d4e2c718fb
Revises: f7c31b9a44d2
Create Date: 2026-07-11 11:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'a9d4e2c718fb'
down_revision: str | None = 'f7c31b9a44d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'leave_recurring_days',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('leave_type_id', sa.UUID(), nullable=False),
        sa.Column('anchor_date', sa.Date(), nullable=False),
        sa.Column('interval_weeks', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('note', sa.Text(), nullable=True),
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
            name=op.f('fk_leave_recurring_days_org_id_orgs'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_leave_recurring_days_user_id_users'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['leave_type_id'], ['leave_types.id'],
            name=op.f('fk_leave_recurring_days_leave_type_id_leave_types'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_recurring_days')),
    )
    op.create_index(
        op.f('ix_leave_recurring_days_org_id'), 'leave_recurring_days', ['org_id'], unique=False
    )
    op.create_index(
        op.f('ix_leave_recurring_days_user_id'), 'leave_recurring_days', ['user_id'],
        unique=False,
    )
    enable_rls('leave_recurring_days')

    op.add_column(
        'leave_requests',
        sa.Column('recurring_day_id', sa.UUID(), nullable=True),
    )
    op.add_column('leave_requests', sa.Column('recurring_date', sa.Date(), nullable=True))
    op.create_foreign_key(
        op.f('fk_leave_requests_recurring_day_id_leave_recurring_days'),
        'leave_requests',
        'leave_recurring_days',
        ['recurring_day_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('fk_leave_requests_recurring_day_id_leave_recurring_days'),
        'leave_requests',
        type_='foreignkey',
    )
    op.drop_column('leave_requests', 'recurring_date')
    op.drop_column('leave_requests', 'recurring_day_id')
    disable_rls('leave_recurring_days')
    op.drop_index(op.f('ix_leave_recurring_days_user_id'), table_name='leave_recurring_days')
    op.drop_index(op.f('ix_leave_recurring_days_org_id'), table_name='leave_recurring_days')
    op.drop_table('leave_recurring_days')
