"""google_gmail_add_manual_poll_at

One nullable column on ``google_connections`` for the manual "Verversen" button (#341): when
this mailbox's owner last *asked* for a poll, which is the cooldown the button is rate-limited
against.

It is deliberately not ``gmail_last_polled_at``. That one answers "how fresh is this feed?"
and is written by the five-minute cron too, so charging a human's first press against a cron
tick thirty seconds ago would make the button read "wait" on a page they just opened. And it
is not enough on its own either: the first poll of a new mailbox baselines and returns early
*without* stamping it, so a freshly connected account could be re-baselined without limit.

Purely additive (docs/WORKFLOW.md): a release predating it never reads the column, and ``NULL``
is exactly the "never manually refreshed" the code already treats as refreshable now.

Revision ID: e5b1c93d7a24
Revises: c4e7b21f9a58
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e5b1c93d7a24"
down_revision: str | None = "c4e7b21f9a58"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "google_connections",
        sa.Column("gmail_manual_poll_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("google_connections", "gmail_manual_poll_at")
