"""tasks_projects_add_unnamed

Create-then-edit (#230) writes a placeholder title and lands the user in edit mode. When they
never finish, the row survives and is indistinguishable from a real record, because the
placeholder *is* an ordinary title — and it was written in the **creator's** locale, so one org
ended up holding both "Naamloze taak" and "Untitled task", sorted into two alphabetical clumps
neither of which is searchable as "the ones nobody named" (#350).

A flag, not a nullable title: every surface that prints a title keeps working, and the flag is
what makes an unnamed row filterable, countable and nameable **in the reader's** locale.

Upgrade path: additive only. ``NOT NULL`` with a server default, so existing rows take ``false``
without a backfill — which is also the honest answer for them. A row created before this release
carries a placeholder title and no way to prove nobody typed it; calling it unnamed on the
strength of a string match would rename a task somebody deliberately called "Naamloze taak".
The previous image ignores the column, so a rollback is safe.

Revision ID: b3f9a17c62d4
Revises: e5b1c93d7a24
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3f9a17c62d4"
down_revision = "e5b1c93d7a24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("tasks", "projects"):
        op.add_column(
            table,
            sa.Column(
                "unnamed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    # Partial: the flag is true for a handful of rows and false for everything else, so a full
    # index would be almost entirely dead weight on the hottest table in the product.
    op.create_index(
        "ix_tasks_org_unnamed", "tasks", ["org_id"], postgresql_where=sa.text("unnamed")
    )
    op.create_index(
        "ix_projects_org_unnamed", "projects", ["org_id"], postgresql_where=sa.text("unnamed")
    )


def downgrade() -> None:
    op.drop_index("ix_projects_org_unnamed", table_name="projects")
    op.drop_index("ix_tasks_org_unnamed", table_name="tasks")
    for table in ("projects", "tasks"):
        op.drop_column(table, "unnamed")
