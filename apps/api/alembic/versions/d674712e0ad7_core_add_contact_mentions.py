"""core_add_contact_mentions

Issue #165: @mentions learn a second kind — contacts. The shared marker gains an optional
discriminator (``mention:contact:<uuid>``; an absent prefix keeps meaning a colleague, so
every stored body parses unchanged, no backfill). The validated contact ids are captured
structurally in a new ``mentioned_contact_ids`` JSONB on both bodies that carry mentions:
task comments and interactions — parallel to ``mentioned_user_ids``, never folded in, so
the notification fan-out stays user-only.

Upgrade path: purely additive — two NOT NULL JSONB columns with a server default of ``[]``,
so existing rows need no backfill. A rolled-back image ignores both.

Revision ID: d674712e0ad7
Revises: 46021f084ee5
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd674712e0ad7'
down_revision: str | None = '46021f084ee5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ('task_comments', 'interactions'):
        op.add_column(
            table,
            sa.Column(
                'mentioned_contact_ids',
                postgresql.JSONB(astext_type=sa.Text()),
                server_default='[]',
                nullable=False,
            ),
        )


def downgrade() -> None:
    for table in ('task_comments', 'interactions'):
        op.drop_column(table, 'mentioned_contact_ids')
