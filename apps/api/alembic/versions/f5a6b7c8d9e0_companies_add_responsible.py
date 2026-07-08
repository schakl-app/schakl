"""companies_add_responsible

Adds ``responsible_user_id`` (verantwoordelijke) to companies — the org member accountable
for the client. Nullable FK → ``users.id`` with ``ON DELETE SET NULL`` so removing a member
never orphans a company. Defaults down onto new projects/tasks in the app layer (CLAUDE.md
§14). Additive DDL only — no data backfill, so no RLS FORCE dance is needed.

Revision ID: f5a6b7c8d9e0
Revises: d3e4f5a6b7c8
Create Date: 2026-07-08 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f5a6b7c8d9e0'
down_revision: str | None = 'd3e4f5a6b7c8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'companies',
        sa.Column(
            'responsible_user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_index(
        op.f('ix_companies_responsible_user_id'),
        'companies',
        ['responsible_user_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_companies_responsible_user_id'), table_name='companies')
    op.drop_column('companies', 'responsible_user_id')
