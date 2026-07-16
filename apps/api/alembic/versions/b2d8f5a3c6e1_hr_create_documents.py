"""hr_create_documents

The employee dossier (personal page): ``hr_documents`` — a typed, per-user index over stored
files (#123) for contract copies, growth plans, bonus agreements, benefits and CAO documents.
Org-scoped and RLS-forced like every domain table; additive with a real downgrade, so
rollback is safe from any released version.

Revision ID: b2d8f5a3c6e1
Revises: a1c7e4d2b9f6
Create Date: 2026-07-16 17:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'b2d8f5a3c6e1'
down_revision: str | None = 'a1c7e4d2b9f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'hr_documents',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('file_id', sa.UUID(), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('uploaded_by_name', sa.String(length=255), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_hr_documents_org_id'), 'hr_documents', ['org_id'])
    op.create_index('ix_hr_documents_user', 'hr_documents', ['org_id', 'user_id'])
    enable_rls('hr_documents')


def downgrade() -> None:
    disable_rls('hr_documents')
    op.drop_index('ix_hr_documents_user', table_name='hr_documents')
    op.drop_index(op.f('ix_hr_documents_org_id'), table_name='hr_documents')
    op.drop_table('hr_documents')
