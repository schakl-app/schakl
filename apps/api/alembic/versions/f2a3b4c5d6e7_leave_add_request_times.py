"""leave_add_request_times

Leave by time of day (#48): ``start_time`` / ``end_time`` on ``leave_requests``.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* Additive, no rewrite, no backfill. ``NULL, NULL`` means "whole scheduled day", which is
  precisely what every existing row is.
* ``hours`` is untouched and stays ``NOT NULL``. Every existing row remains a valid request.
* **Rollback-safe, with a caveat worth writing down:** the previous image does not select
  ``start_time``/``end_time``, so it renders a part-day request as a whole-day one — with the
  *correct* ``hours``, because ``hours`` is stored. Degraded display, not corruption. Note it in
  the release notes.
* **No retroactive recalculation** of existing requests. Their ``hours`` are what people agreed
  to. The service recomputes on edit only.
* ``TIME``, not ``TIMESTAMPTZ``: leave is a local-calendar concept. "I'm off from 15:00" means
  15:00 where the employee works, whether or not the clocks changed that weekend, and a whole
  day would have to invent a time.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-10 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: str | None = 'e1f2a3b4c5d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('leave_requests', sa.Column('start_time', sa.Time(), nullable=True))
    op.add_column('leave_requests', sa.Column('end_time', sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column('leave_requests', 'end_time')
    op.drop_column('leave_requests', 'start_time')
