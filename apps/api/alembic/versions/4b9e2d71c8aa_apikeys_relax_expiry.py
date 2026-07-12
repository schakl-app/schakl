"""apikeys_relax_expiry — expires_at becomes nullable (NULL = never expires).

The owner decided unlimited keys should be a choice: a nullable expiry is an *expand* —
old code never writes NULL, and every existing row keeps its date, so rollback stays safe.

Revision ID: 4b9e2d71c8aa
Revises: 98a7fe4c7d40
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "4b9e2d71c8aa"
down_revision = "98a7fe4c7d40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "api_keys",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    # Give any unlimited keys a real expiry so the NOT NULL can come back; a year out mirrors
    # the creation-time maximum the previous release enforced.
    op.execute(
        "UPDATE api_keys SET expires_at = now() + interval '366 days' WHERE expires_at IS NULL"
    )
    op.alter_column(
        "api_keys",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
