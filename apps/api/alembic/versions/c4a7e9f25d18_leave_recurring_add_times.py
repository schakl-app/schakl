"""leave_recurring_add_times

Part-day recurring free days (#107 follow-up): "every Wednesday off from 15:00". Two nullable
wall-clock ``TIME`` columns on ``leave_recurring_days``, mirroring ``leave_requests`` —
``NULL`` means the whole scheduled day, same as everywhere else (#48), and generated requests
carry the window so the server prices exactly the hours inside it.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive.** Two nullable columns; every existing pattern reads as whole-day, which
  is exactly what it was. Applies on top of any released ``head``.
* **Rollback-safe.** The previous image never selects these columns.

Revision ID: c4a7e9f25d18
Revises: b3f8c1d92e47
Create Date: 2026-07-12 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a7e9f25d18'
down_revision: str | None = 'b3f8c1d92e47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('leave_recurring_days', sa.Column('start_time', sa.Time(), nullable=True))
    op.add_column('leave_recurring_days', sa.Column('end_time', sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column('leave_recurring_days', 'end_time')
    op.drop_column('leave_recurring_days', 'start_time')
