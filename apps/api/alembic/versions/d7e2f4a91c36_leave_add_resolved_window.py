"""leave_add_resolved_window

Snapshot of the clock window a timed leave request covers (#107 follow-up): omitted bounds
resolved from the schedule **at write time**, so the calendar shows the window that was
actually priced — and a later schedule change never rewrites how past leave displays. The
same snapshot-at-write principle as actor names (#64): display facts of the past are
written when they happen, never live-joined.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive.** Two nullable ``TIME`` columns, no backfill: a pre-existing timed row
  has no snapshot and the team feed falls back to resolving against the *current* schedule —
  exactly what it did before this column existed, so nobody's display regresses. New writes
  and edits fill the snapshot from then on.
* **Rollback-safe.** The previous image never selects these columns.

Revision ID: d7e2f4a91c36
Revises: c4a7e9f25d18
Create Date: 2026-07-12 15:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e2f4a91c36'
down_revision: str | None = 'c4a7e9f25d18'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('leave_requests', sa.Column('resolved_start_time', sa.Time(), nullable=True))
    op.add_column('leave_requests', sa.Column('resolved_end_time', sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column('leave_requests', 'resolved_end_time')
    op.drop_column('leave_requests', 'resolved_start_time')
