"""time_add_approval

Hours approval + invoicing flow: a manager approves entries (``approved_at`` /
``approved_by_user_id``) and marks billable hours as invoiced (``invoiced_at``).

Revision ID: 6a7b8c9d0e1f
Revises: 5f6a7b8c9d0e
Create Date: 2026-07-07 16:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a7b8c9d0e1f'
down_revision: str | None = '5f6a7b8c9d0e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'time_entries',
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'time_entries',
        sa.Column('approved_by_user_id', sa.UUID(), nullable=True),
    )
    op.add_column(
        'time_entries',
        sa.Column('invoiced_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        op.f('fk_time_entries_approved_by_user_id_users'),
        'time_entries',
        'users',
        ['approved_by_user_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('fk_time_entries_approved_by_user_id_users'), 'time_entries', type_='foreignkey'
    )
    op.drop_column('time_entries', 'invoiced_at')
    op.drop_column('time_entries', 'approved_by_user_id')
    op.drop_column('time_entries', 'approved_at')
