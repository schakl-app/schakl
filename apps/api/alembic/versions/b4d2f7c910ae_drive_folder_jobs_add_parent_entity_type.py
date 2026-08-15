"""drive_folder_jobs_add_parent_entity_type

Revision ID: b4d2f7c910ae
Revises: f31c8a5d7e04
Create Date: 2026-08-13 12:00:00.000000

A task can now be given its own Drive folder (#328), and it nests under its project's folder
— else its client's. The outbox row already carried *which record* the new folder nests under
(``parent_entity_id``) but not *what kind*: the worker read it as a company, because a project
under its client was the only nesting that existed (#150).

Purely **additive** and deliberately **not backfilled**. ``NULL`` reads as ``company`` in the
worker, which is exactly what every existing row means, and is also what docs/WORKFLOW.md's
expand/contract rule needs during a rolling deploy: an API replica still running the previous
image keeps writing project jobs with no ``parent_entity_type``, and the new worker still nests
them under the client. Rolling back is a no-op too — the old worker ignores a column it does
not select, and the only rows carrying a non-NULL value are task jobs it never had a route to
create.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4d2f7c910ae"
down_revision: str | Sequence[str] | None = "f31c8a5d7e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drive_folder_jobs",
        sa.Column("parent_entity_type", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("drive_folder_jobs", "parent_entity_type")
