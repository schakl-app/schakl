"""tasks_comment_mentions

@mentions in task comments (issue #63). Adds ``task_comments.mentioned_user_ids`` — a JSONB array
of the user ids mentioned in the body, extracted from the `@[Name](mention:<uuid>)` markers by the
service. Additive and non-destructive: a nullable-free column with a ``'[]'`` server default, so a
populated table takes it without a rewrite and the previous image (which never reads it) still runs.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-11 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: str | None = 'd1e2f3a4b5c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'task_comments',
        sa.Column(
            'mentioned_user_ids',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('task_comments', 'mentioned_user_ids')
