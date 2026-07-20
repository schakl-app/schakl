"""google_add_gmail_log_internal

Revision ID: e2f6a8c4d9b1
Revises: c7d1e9f3a2b8
Create Date: 2026-07-20 00:00:00.000000

Additive expand: the opt-in that lets the gmail feed also ingest colleague-to-colleague mail
(always pending, filed onto a client/project at approval). Server default ``false`` keeps every
existing org on today's behaviour; the previous release never reads it, so rollback stays safe.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2f6a8c4d9b1'
down_revision: str | None = 'c7d1e9f3a2b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'google_settings',
        sa.Column(
            'gmail_log_internal',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('google_settings', 'gmail_log_internal')
