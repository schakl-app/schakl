"""tasks: an occurrence names the rule it was generated from

A repeat rule in schedule mode used to hand itself to the next occurrence the night it fell
due, so at any moment exactly one task in a chain existed and carried the rule. The rule now
materializes its occurrences a **year ahead** (``recurrence.materialize_series``), which makes
"the tasks of this series" a question the database must be able to answer: the assignee
hand-off that transfers the future, and a rule change that re-lays it, both find their
siblings through this column.

Purely additive and safe for an unattended self-host upgrade (docs/WORKFLOW.md): the column is
nullable, no row is rewritten, and a carrier stored before this release simply *is* a series
root now — the nightly sweep fills the year in from its ``recurrence_next_run``. ``SET NULL``
on delete, because an occurrence outlives its rule as an ordinary task.

Revision ID: c3f8a2d7e1b4
Revises: b7d2e4f1a9c3
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision = "c3f8a2d7e1b4"
down_revision = "b7d2e4f1a9c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "recurrence_source_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey(
                "tasks.id",
                name="fk_tasks_recurrence_source_id_tasks",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    # The series lookup: "every occurrence of this root", tenant first. Partial, because the
    # overwhelming majority of tasks belong to no series at all.
    op.create_index(
        "ix_tasks_org_recurrence_source",
        "tasks",
        ["org_id", "recurrence_source_id"],
        postgresql_where=sa.text("recurrence_source_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_org_recurrence_source", table_name="tasks")
    op.drop_column("tasks", "recurrence_source_id")
