"""dashboard_add_prefs_columns

Revision ID: f31c8a5d7e04
Revises: c4e71a9d2b58
Create Date: 2026-08-13 09:00:00.000000

The My Day board's two columns become storage instead of arithmetic (#325).

Until now ``dashboard_prefs.widgets`` was the whole layout and the browser cut it in two at
``ceil(n/2)`` on every render. That made a tile's column a function of its index: a drag
across only "took" if it also crossed that index, and crossing it shoved whatever sat on the
boundary the other way. Adding a widget from the gallery re-cut the board for the same reason.

Purely **additive**, and deliberately **not backfilled**. NULL says *nobody has arranged
columns here*, which is a different sentence from "one empty column" — and it is the sentence
that makes this upgrade a no-op for every saved layout: the web keeps splitting a NULL row at
the halfway point exactly as it does today, so a member's board looks the same the morning
after the upgrade and only changes once they drag something. Writing today's computed split
into the column would instead freeze one release's rendering rule as if a person had chosen it.

``widgets`` stays authoritative for the flat reading order (a phone renders it, and the
previous release reads nothing else), so docs/WORKFLOW.md's expand/contract rule has nothing
to contract: rolling the image back leaves old code reading a column it already understood,
and rolling forward mid-deploy leaves the old replica serving a board it can still draw.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f31c8a5d7e04"
down_revision: str | Sequence[str] | None = "c4e71a9d2b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dashboard_prefs",
        sa.Column("columns", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dashboard_prefs", "columns")
