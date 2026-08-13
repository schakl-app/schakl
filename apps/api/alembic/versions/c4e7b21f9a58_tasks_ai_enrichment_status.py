"""tasks_add_ai_enrichment_status

Two nullable columns on ``tasks`` for "let schakl fill this in from the email" (#327).

Purely additive, so it needs no expand/contract dance (docs/WORKFLOW.md): a release that
predates it simply never reads the columns, and every existing row keeps ``NULL``, which is
exactly the "no AI run has ever touched this task" the code already treats as the default.

Revision ID: c4e7b21f9a58
Revises: b4d2f7c910ae
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c4e7b21f9a58"
down_revision: str | None = "b4d2f7c910ae"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("ai_status", sa.String(length=16), nullable=True))
    op.add_column(
        "tasks", sa.Column("ai_status_at", sa.DateTime(timezone=True), nullable=True)
    )
    # The reaper scans only what a worker currently claims — never the whole table.
    op.create_index(
        "ix_tasks_ai_status_running",
        "tasks",
        ["org_id", "ai_status_at"],
        unique=False,
        postgresql_where=sa.text("ai_status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_ai_status_running", table_name="tasks")
    op.drop_column("tasks", "ai_status_at")
    op.drop_column("tasks", "ai_status")
