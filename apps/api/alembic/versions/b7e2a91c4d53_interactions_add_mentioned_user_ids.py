"""interactions_add_mentioned_user_ids

Revision ID: b7e2a91c4d53
Revises: a9e1f0d4c2b7
Create Date: 2026-07-12 00:00:00.000000

Additive expand (#151): the users @mentioned in a manual contactmoment note, captured
structurally like ``task_comments.mentioned_user_ids`` (#63) so a render never re-parses the
body. ``'[]'`` default backfills existing rows in place. Reversible; the previous release
never reads the column, so rollback stays safe.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7e2a91c4d53"
down_revision: str | None = "a9e1f0d4c2b7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interactions",
        sa.Column(
            "mentioned_user_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("interactions", "mentioned_user_ids")
