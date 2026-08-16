"""google_ads: record whether an account's thirteen months were ever filled

One nullable timestamp on ``google_ads_accounts``. Under the rules ``docs/WORKFLOW.md`` sets for
a schema change that runs unattended on somebody else's production data:

- **Which released versions upgrade into this?** Any at or after ``c3d9f4a17b62``. Nothing here
  reads or reshapes an existing column.
- **What happens to existing rows?** They get ``NULL``, and that is the *load-bearing* part
  rather than a default nobody thought about. ``NULL`` means "the backfill has not finished",
  which is true of every row that exists today — the job was a one-off enqueued at link time and
  never verified, and on the live instance not one of thirteen accounts had more than eleven
  days of history. The nightly sync reads this column and queues the fill for anything unstamped,
  so the upgrade repairs itself on the first night rather than needing anyone to press anything.
  The cost of being wrong in this direction is one extra backfill against Google's quota; the
  cost of the other (defaulting to ``now()``) is that every existing hole stays a hole for ever.
- **Is it reversible?** Yes: ``downgrade`` drops the column. The nightly enqueue is the only
  reader, and the older image does not have it.
- **Can the previous image run against the new schema?** Yes. The API rolls ``start-first``, so
  both images serve against this schema for the length of every deploy; the old one never selects
  or writes this column, and it is nullable so its inserts still satisfy the table.

Revision ID: d41b7a0c9e35
Revises: c3d9f4a17b62
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d41b7a0c9e35"
down_revision = "c3d9f4a17b62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "google_ads_accounts",
        sa.Column("backfilled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("google_ads_accounts", "backfilled_at")
