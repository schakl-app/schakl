"""interactions_widen_mailbox_ids

Revision ID: a3c9e17f5b2d
Revises: d4a9b3c6f2e7
Create Date: 2026-09-06 00:00:00.000000

``interactions.gmail_message_id`` / ``gmail_thread_id`` become the *mailbox provider's* message
and thread ids for every connected-mailbox row (``MAILBOX_SOURCES``): the ``microsoft``
integration logs Outlook mail into the same columns, and a Graph message id is a ~150-character
base64 string where Gmail's is a 16-character hex one. Widening a ``varchar`` is a catalog-only
change in Postgres (no rewrite, no lock beyond the DDL), and the unique/plain indexes on the
columns carry over unchanged. Expand-only: older code reads and writes the columns exactly as
before, so rolling the image back is safe without a downgrade.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3c9e17f5b2d'
down_revision: str | None = 'd4a9b3c6f2e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'interactions',
        'gmail_message_id',
        existing_type=sa.String(length=64),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
    op.alter_column(
        'interactions',
        'gmail_thread_id',
        existing_type=sa.String(length=64),
        type_=sa.String(length=512),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Narrowing would fail on any Outlook row; those rows belong to the integration this
    # revision exists for, so a downgrade removes them first rather than refusing.
    op.execute("DELETE FROM interactions WHERE source = 'outlook'")
    op.alter_column(
        'interactions',
        'gmail_thread_id',
        existing_type=sa.String(length=512),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
    op.alter_column(
        'interactions',
        'gmail_message_id',
        existing_type=sa.String(length=512),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
