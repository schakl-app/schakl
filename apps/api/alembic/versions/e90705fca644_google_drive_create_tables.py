"""google_drive_create_tables

Revision ID: e90705fca644
Revises: 2b43709c5d77
Create Date: 2026-07-12 00:00:00.000000

New google.drive tables (issue #21): file/folder references and the folder-provisioning
outbox. Expand-only: additive DDL, nothing else references them, older code never reads
them — rollback (downgrade drops both + their RLS policies) is safe from any released version.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'e90705fca644'
down_revision: str | None = '2b43709c5d77'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        'drive_links',
        sa.Column('entity_type', sa.String(length=32), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('drive_file_id', sa.String(length=128), nullable=False),
        sa.Column('drive_url', sa.String(length=500), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('mime_type', sa.String(length=255), nullable=True),
        sa.Column('is_folder', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('shared_drive_id', sa.String(length=128), nullable=True),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'], ['users.id'],
            name=op.f('fk_drive_links_created_by_user_id_users'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_drive_links_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_drive_links')),
        sa.UniqueConstraint(
            'org_id', 'entity_type', 'entity_id', 'drive_file_id',
            name='uq_drive_links_org_entity_file',
        ),
    )
    op.create_index(op.f('ix_drive_links_org_id'), 'drive_links', ['org_id'])
    op.create_index(
        'ix_drive_links_org_entity', 'drive_links', ['org_id', 'entity_type', 'entity_id']
    )
    enable_rls('drive_links')

    op.create_table(
        'drive_folder_jobs',
        sa.Column('entity_type', sa.String(length=32), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('parent_entity_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_drive_folder_jobs_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_drive_folder_jobs')),
        sa.UniqueConstraint(
            'org_id', 'entity_type', 'entity_id', name='uq_drive_folder_jobs_org_entity'
        ),
    )
    op.create_index(op.f('ix_drive_folder_jobs_org_id'), 'drive_folder_jobs', ['org_id'])
    op.create_index(
        'ix_drive_folder_jobs_org_status', 'drive_folder_jobs', ['org_id', 'status']
    )
    enable_rls('drive_folder_jobs')


def downgrade() -> None:
    disable_rls('drive_folder_jobs')
    op.drop_index('ix_drive_folder_jobs_org_status', table_name='drive_folder_jobs')
    op.drop_index(op.f('ix_drive_folder_jobs_org_id'), table_name='drive_folder_jobs')
    op.drop_table('drive_folder_jobs')
    disable_rls('drive_links')
    op.drop_index('ix_drive_links_org_entity', table_name='drive_links')
    op.drop_index(op.f('ix_drive_links_org_id'), table_name='drive_links')
    op.drop_table('drive_links')
