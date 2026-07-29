"""dashboard_time_add_hot_path_indexes

The interactive dashboard, task list and timesheet filter tenant data by the same compound
dimensions on every navigation. The original single-column indexes force PostgreSQL to combine
indexes (or scan one and filter the rest). Add indexes matching those request shapes.

Revision ID: b4c92d18e6f3
Revises: a3f81c07d5e2
Create Date: 2026-07-29 23:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4c92d18e6f3"
down_revision: str | None = "a3f81c07d5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_tasks_org_status",
        "tasks",
        ["org_id", "status"],
    )
    op.create_index(
        "ix_tasks_org_assignee_status_due",
        "tasks",
        ["org_id", "assignee_user_id", "status", "due_date"],
    )
    op.create_index(
        "ix_time_entries_org_user_started",
        "time_entries",
        ["org_id", "user_id", "started_at"],
    )
    op.create_index(
        "ix_time_entries_org_started",
        "time_entries",
        ["org_id", "started_at"],
    )
    op.create_index(
        "ix_time_entries_org_project_started",
        "time_entries",
        ["org_id", "project_id", "started_at"],
    )
    op.create_index(
        "ix_time_entries_running",
        "time_entries",
        ["org_id", "user_id"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_time_entries_running", table_name="time_entries")
    op.drop_index("ix_time_entries_org_project_started", table_name="time_entries")
    op.drop_index("ix_time_entries_org_started", table_name="time_entries")
    op.drop_index("ix_time_entries_org_user_started", table_name="time_entries")
    op.drop_index("ix_tasks_org_assignee_status_due", table_name="tasks")
    op.drop_index("ix_tasks_org_status", table_name="tasks")
