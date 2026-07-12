"""core_create_files

Revision ID: 7dc80932d0e0
Revises: ae1ab3cfb937
Create Date: 2026-07-12 00:00:00.000000

New ``files`` metadata table for the pluggable storage core (#123). Expand-only: additive DDL,
no backfill, nothing else references it, and older code never reads it — rollback (downgrade
drops the table + its RLS policy) is safe from any released version. The *bytes* live on the
storage volume; only this row is tenant-scoped, which is what keeps Golden Rule 1 true for
uploads (a filesystem has no RLS; the row that gates access does).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = '7dc80932d0e0'
down_revision: str | None = 'ae1ab3cfb937'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'files',
        sa.Column('backend', sa.String(length=20), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=120), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=True),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'], ['users.id'],
            name=op.f('fk_files_created_by_user_id_users'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_files_org_id_orgs'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_files')),
    )
    op.create_index(op.f('ix_files_org_id'), 'files', ['org_id'], unique=False)
    enable_rls('files')


def downgrade() -> None:
    disable_rls('files')
    op.drop_index(op.f('ix_files_org_id'), table_name='files')
    op.drop_table('files')
