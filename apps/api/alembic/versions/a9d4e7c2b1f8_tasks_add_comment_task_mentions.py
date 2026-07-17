"""tasks_add_comment_task_mentions

#task references in task comments (issue #197). Adds ``task_comments.mentioned_task_ids`` — a
JSONB array of the task ids referenced in the body, extracted from the
`#[Title](mention:task:<uuid>)` markers by the service and validated org-scoped. Additive and
non-destructive, the exact shape of e2f3a4b5c6d7 (#63) and the contact-mentions column (#165):
a ``'[]'`` server default so a populated table takes it without a rewrite, and the previous
image (which never reads it) still runs — rollback safe.

Revision ID: a9d4e7c2b1f8
Revises: c4e1b8a6f025
Create Date: 2026-07-16 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a9d4e7c2b1f8'
down_revision: str | None = 'a7c3e9d21b45'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'task_comments',
        sa.Column(
            'mentioned_task_ids',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('task_comments', 'mentioned_task_ids')
