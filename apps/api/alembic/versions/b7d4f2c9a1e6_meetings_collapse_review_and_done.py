"""meetings: ``review`` and ``done`` become ``ready`` — the confirm step is gone

Data only. The minutes are editable for as long as the meeting exists, and the contact moment
is written the moment the draft lands rather than when a person presses confirm, so the two
states the confirm sat between are one state. Rows already on ``review`` carry minutes and no
contact moment: the page files them on the first edit (or the button), which is why nothing is
written for them here. ``confirmed_at`` stays for a release, read by nothing.

Revision ID: b7d4f2c9a1e6
Revises: c5e8f1a2b7d3
Create Date: 2026-09-24
"""

from __future__ import annotations

from alembic import op

revision = "b7d4f2c9a1e6"
down_revision = "c5e8f1a2b7d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Under ``FORCE ROW LEVEL SECURITY`` with no org GUC bound an unqualified UPDATE matches zero
    # rows silently (``87e32dccc095``). Shipped without this dance first; ``e8a2c5f9d4b1``
    # repairs the instances that ran the silent version.
    op.execute("ALTER TABLE meetings NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE meetings SET status = 'ready' WHERE status IN ('review', 'done')")
    op.execute("ALTER TABLE meetings FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE meetings NO FORCE ROW LEVEL SECURITY")
    # The old split was "has a contact moment yet": a row filed on the timeline reads as
    # confirmed, one without as still under review.
    op.execute(
        "UPDATE meetings SET status = CASE WHEN interaction_id IS NULL THEN 'review' "
        "ELSE 'done' END WHERE status = 'ready'"
    )
    op.execute("ALTER TABLE meetings FORCE ROW LEVEL SECURITY")
