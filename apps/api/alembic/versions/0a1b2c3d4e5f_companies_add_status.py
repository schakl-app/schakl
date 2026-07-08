"""companies_add_status

Adds the client lifecycle ``status`` to companies (lead → onboarding → active → offboarding
→ archived). Existing rows are backfilled as ``active`` (they are the current client book);
new rows default in the app layer. Status transitions drive task-template automation.

Revision ID: 0a1b2c3d4e5f
Revises: f4d5e6a7b8c9
Create Date: 2026-07-07 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a1b2c3d4e5f'
down_revision: str | None = 'f4d5e6a7b8c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'companies',
        sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
    )
    op.create_index(op.f('ix_companies_status'), 'companies', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_companies_status'), table_name='companies')
    op.drop_column('companies', 'status')
