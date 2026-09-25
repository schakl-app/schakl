"""meetings: where a recording was interrupted and taken up again

A capture the phone takes away (a locked screen, a frozen tab, an incoming call) used to end
the meeting; the recorder now starts a new ``MediaRecorder`` on the same meeting and the worker
joins the sessions into one file. The joined recording has one continuous clock, so what the
interruption cost has to be stated beside it: ``recording_gaps`` is
``[{"at": <second of the recording>, "seconds": <not captured>}]``, measured from the pieces'
arrival times and lengths at fold time. Additive; ``NULL`` for every recording so far.

Revision ID: f1a7c3e9b2d5
Revises: a3d7f2c9e5b8
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f1a7c3e9b2d5"
down_revision = "a3d7f2c9e5b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("recording_gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meetings", "recording_gaps")
