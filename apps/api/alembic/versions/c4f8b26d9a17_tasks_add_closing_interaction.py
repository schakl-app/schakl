"""tasks_add_closing_interaction

Revision ID: c4f8b26d9a17
Revises: b7e2a91c4d53
Create Date: 2026-07-12 00:00:00.000000

Additive expand (#157): the contact moment a task was closed with (nullable UUID, no FK —
``interactions.task_id`` already points the other way and a mutual FK is circular; the
service validates linkage), and a per-status ``requires_interaction`` policy flag defaulting
false so nothing changes for existing vocabularies. Reversible; the previous release never
reads either column.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4f8b26d9a17"
down_revision: str | None = "b7e2a91c4d53"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("closing_interaction_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "task_statuses",
        sa.Column(
            "requires_interaction",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("task_statuses", "requires_interaction")
    op.drop_column("tasks", "closing_interaction_id")
