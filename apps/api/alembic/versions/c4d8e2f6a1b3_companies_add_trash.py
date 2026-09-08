"""companies_add_trash

Revision ID: c4d8e2f6a1b3
Revises: b4d7e2a9c1f3
Create Date: 2026-09-08 09:00:00.000000

The trash can (docs/TRASH.md): deleting a client stamps ``deleted_at`` instead of running the
cascade that, until now, took its invoices, quotes, subscriptions and domains with it. Three
nullable columns — when, and who as an id while the account exists and as a name for after it
does not (§16) — plus a partial index over the rows that are actually in the trash, which is
what the trash screen lists and what every dependent list anti-joins against.

Additive and reversible: no row is touched, ``NULL`` is "live" and is what every existing row
reads, and a downgrade drops the columns and nothing else.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d8e2f6a1b3'
down_revision: str | None = 'b4d7e2a9c1f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('companies', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'companies',
        sa.Column('deleted_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column('companies', sa.Column('deleted_by_name', sa.String(length=200), nullable=True))
    op.create_foreign_key(
        'fk_companies_deleted_by_user_id_users',
        'companies',
        'users',
        ['deleted_by_user_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_companies_trashed',
        'companies',
        ['org_id', 'deleted_at'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_companies_trashed', table_name='companies')
    op.drop_constraint('fk_companies_deleted_by_user_id_users', 'companies', type_='foreignkey')
    op.drop_column('companies', 'deleted_by_name')
    op.drop_column('companies', 'deleted_by_user_id')
    op.drop_column('companies', 'deleted_at')
