"""perf_add_list_and_panel_indexes

The company hub, the project detail and the leave ledger each narrow a large table by a
*parent* first and only then by state or date. The existing composite indexes all start with a
different second column — ``(org_id, status)``, ``(org_id, project_id, started_at)``,
``(org_id, assignee_user_id, …)`` — and a leftmost prefix cannot be skipped, so those reads
fell back to combining single-column indexes or scanning one and filtering the rest (#290).

Deliberately *not* added, having checked the query shapes rather than the intuition:
``companies(org_id, name)`` and ``contacts(org_id, last_name, first_name)``. Both lists sort
through ``func.lower(...)``, which a plain btree on the raw column cannot serve, and both
default to ``created_at DESC``. An index that the planner will not choose is pure write cost.

Revision ID: c7e1a4d90b26
Revises: f5733e3dae47
Create Date: 2026-07-30 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c7e1a4d90b26"
down_revision: str | None = "f5733e3dae47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (name, table, columns) — declared identically in each model's ``__table_args__``.
_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    # The client's own hours: the company panel's recent list and its total.
    ("ix_time_entries_org_company_started", "time_entries", ["org_id", "company_id", "started_at"]),
    # One client's timeline, newest first — the panel and the `?company_id=` filter.
    (
        "ix_interactions_org_company_occurred",
        "interactions",
        ["org_id", "company_id", "occurred_at"],
    ),
    # A parent's unfinished work: the company hub and the project detail.
    ("ix_tasks_org_company_status", "tasks", ["org_id", "company_id", "status"]),
    ("ix_tasks_org_project_status", "tasks", ["org_id", "project_id", "status"]),
    # Deadline windows that belong to nobody in particular: "due this week", the reminder cron.
    ("ix_tasks_org_due", "tasks", ["org_id", "due_date"]),
    # "This employee, this year" — the pot ledger, the balance, the overlap check.
    ("ix_leave_requests_org_user_start", "leave_requests", ["org_id", "user_id", "start_date"]),
)


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _ in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
