"""meetings: the participants roster — who was in the room, linked to staff or a client contact

Purely additive. ``meetings.speakers`` (``{"S1": "Jan"}``) stays for the rows already written and
is read as a name-only roster where ``participants`` is ``NULL``; every new write lands here.

Revision ID: e5a8c3d7f1b2
Revises: d4e7f2a9c1b6
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e5a8c3d7f1b2"
down_revision = "d4e7f2a9c1b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("participants", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meetings", "participants")
