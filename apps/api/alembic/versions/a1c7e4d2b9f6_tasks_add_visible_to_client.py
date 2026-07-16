"""tasks_add_visible_to_client

Client-portal task visibility: ``tasks.visible_to_client`` — off by default, so nothing is
exposed until staff tick a task. Additive with a server default on a populated table (no
rewrite, no NOT NULL failure) and a real downgrade; the previous image never reads it.

Revision ID: a1c7e4d2b9f6
Revises: f7c4d9a1b8e3
Create Date: 2026-07-16 16:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c7e4d2b9f6'
down_revision: str | None = 'f7c4d9a1b8e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'tasks',
        sa.Column('visible_to_client', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('tasks', 'visible_to_client')
