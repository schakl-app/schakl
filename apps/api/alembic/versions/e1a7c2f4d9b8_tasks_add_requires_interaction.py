"""tasks_add_requires_interaction

Revision ID: e1a7c2f4d9b8
Revises: e5c2a9f4d180
Create Date: 2026-07-14 00:00:00.000000

Additive expand (#157 extended): a per-task and per-template ``requires_interaction`` policy
flag. A flagged task may only reach a finished status once a designated closing contact moment
is linked, independent of the per-status flag; a flagged template item copies the policy onto
the tasks it spawns. Both default false, so nothing changes for existing tasks and templates.
Reversible; the previous release never reads either column.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1a7c2f4d9b8"
down_revision: str | None = "e5c2a9f4d180"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "requires_interaction",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "task_template_items",
        sa.Column(
            "requires_interaction",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("task_template_items", "requires_interaction")
    op.drop_column("tasks", "requires_interaction")
