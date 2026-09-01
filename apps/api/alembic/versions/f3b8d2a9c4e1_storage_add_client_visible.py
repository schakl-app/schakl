"""storage_add_client_visible

Revision ID: f3b8d2a9c4e1
Revises: 7502c9155ca8
Create Date: 2026-09-01 12:00:00.000000

A per-file "may the client see this" bit on ``files`` (the image-attachment research task).
Additive and reversible: ``NOT NULL DEFAULT false`` with a server default, so every existing
row — a screenshot on a task, a brief on a project — comes up **hidden from the client portal**,
which is the posture the task page already enforced in the web and the API never did. Nothing
is backfilled to ``true`` on purpose: which attachments a client may read is a decision only
the agency can make, and the previous image still reads the column-less row fine (the column
is unknown to it, and it never filtered on it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3b8d2a9c4e1"
down_revision: str | None = "7502c9155ca8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column(
            "client_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("files", "client_visible")
