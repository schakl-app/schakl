"""core_add_show_brand_name

White-label option: hide the brand name text next to the logo (many logos already contain
the wordmark).

Revision ID: a0b1c2d3e4f5
Revises: 9d0e1f2a3b4c
Create Date: 2026-07-08 16:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0b1c2d3e4f5'
down_revision: str | None = '9d0e1f2a3b4c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org_settings',
        sa.Column('show_brand_name', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('org_settings', 'show_brand_name')
