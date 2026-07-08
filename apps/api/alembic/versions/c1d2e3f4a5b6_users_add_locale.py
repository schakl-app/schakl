"""users_add_locale

Adds a per-user display-language preference (CLAUDE.md §8). ``users`` is a global identity
table (no ``org_id``, no RLS), so this is a plain nullable column.

Revision ID: c1d2e3f4a5b6
Revises: ebf05ea23751
Create Date: 2026-07-07 13:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: str | None = 'ebf05ea23751'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('locale', sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'locale')
