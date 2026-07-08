"""projects_add_budget_period

Hour budgets can now apply to the whole project ("total") or reset every month ("monthly").

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-08 18:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: str | None = 'b1c2d3e4f5a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'projects',
        sa.Column('budget_period', sa.String(length=10), server_default='total', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('projects', 'budget_period')
