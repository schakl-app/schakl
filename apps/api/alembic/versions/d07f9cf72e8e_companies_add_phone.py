"""companies_add_phone

Revision ID: d07f9cf72e8e
Revises: e2f6a8c4d9b1
Create Date: 2026-07-21 00:00:00.000000

Additive expand (issue #256): a nullable E.164 phone column on companies. No backfill — the
column is new, every existing row is legitimately NULL. The previous release never reads it,
so rolling the image tag back stays safe. ``contacts.phone`` needs no migration: it already
exists (String(64)); only its *write path* gains validation, and pre-existing freeform values
are deliberately left untouched until someone edits them.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd07f9cf72e8e'
down_revision: str | None = 'e2f6a8c4d9b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('companies', sa.Column('phone', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('companies', 'phone')
