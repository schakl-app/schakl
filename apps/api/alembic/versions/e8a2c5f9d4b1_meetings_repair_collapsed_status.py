"""meetings: repair ``review`` / ``done`` rows the collapse migration never reached

``b7d4f2c9a1e6`` ran its UPDATE as the table owner under ``FORCE ROW LEVEL SECURITY`` with no org
GUC bound, where an unqualified UPDATE matches zero rows *silently* (``87e32dccc095``). Every
instance that upgraded kept its old statuses, and since ``MeetingStatus`` no longer knows them
the list endpoint failed to serialise its first such row — every existing meeting disappeared
from the screen. This repeats the conversion with RLS lifted for the statement. Idempotent: an
instance whose rows were already converted matches nothing.

Revision ID: e8a2c5f9d4b1
Revises: a3d8f1c6e2b7
Create Date: 2026-09-25
"""

from __future__ import annotations

from alembic import op

revision = "e8a2c5f9d4b1"
down_revision = "a3d8f1c6e2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE meetings NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE meetings SET status = 'ready' WHERE status IN ('review', 'done')")
    op.execute("ALTER TABLE meetings FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Nothing to undo: ``b7d4f2c9a1e6``'s downgrade restores the split.
    pass
