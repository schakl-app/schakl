"""leave_add_hours_override

The manager override (#48): ``hours_override`` + ``hours_override_by_user_id``.

Once the server owns the hour calculation, ``hours`` stops being a client input — and real life
still has an employee who agrees to take four hours for a day they were not scheduled. Issue #48
demands the choice be made explicitly rather than leaving the field half-trusted: **build it**,
because refusing it makes that case inexpressible (a non-working day computes to zero hours and
the request is rejected), and because an unattributed override is worse than none.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* Additive, both nullable, no backfill. ``NULL`` — the ordinary case — means ``hours`` is exactly
  what ``compute_hours`` returned, which is true of every pre-#48 row by construction: nobody
  could override anything before this.
* Rollback-safe: the previous image selects neither column, and ``hours`` still carries the
  agreed number.
* The FK is ``ON DELETE SET NULL``: removing the manager who set an override must not take the
  request with them.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-07-10 13:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: str | None = 'f2a3b4c5d6e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_requests', sa.Column('hours_override', sa.Numeric(precision=6, scale=2), nullable=True)
    )
    op.add_column('leave_requests', sa.Column('hours_override_by_user_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f('fk_leave_requests_hours_override_by_user_id_users'),
        'leave_requests',
        'users',
        ['hours_override_by_user_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('fk_leave_requests_hours_override_by_user_id_users'),
        'leave_requests',
        type_='foreignkey',
    )
    op.drop_column('leave_requests', 'hours_override_by_user_id')
    op.drop_column('leave_requests', 'hours_override')
