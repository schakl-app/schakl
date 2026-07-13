"""time_add_entry_types

Issue #176: tenant-configurable time-entry types (the contact-types shape: key +
label_i18n + position + active) and an optional ``entry_type_key`` on ``time_entries``.
Existing entries stay untyped (NULL) — no backfill, no remap. Defaults (``work``,
``email``) seed lazily per org on first use, the interaction-kinds pattern, so this
migration seeds nothing.

Upgrade path: purely additive — one new table, one nullable column. A rolled-back image
ignores both. Safe to run unattended on a populated database.

Revision ID: ae35d2d58654
Revises: 8667942b764c
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'ae35d2d58654'
down_revision: str | None = '8667942b764c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'time_entry_types',
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('label_i18n', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_time_entry_types_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_time_entry_types')),
        sa.UniqueConstraint('org_id', 'key', name='uq_time_entry_types_org_key'),
    )
    op.create_index(op.f('ix_time_entry_types_org_id'), 'time_entry_types',
                    ['org_id'], unique=False)
    # Tenant isolation at the database layer too (Golden Rule 1).
    enable_rls('time_entry_types')

    op.add_column('time_entries', sa.Column('entry_type_key', sa.String(length=50),
                                            nullable=True))


def downgrade() -> None:
    op.drop_column('time_entries', 'entry_type_key')

    disable_rls('time_entry_types')
    op.drop_index(op.f('ix_time_entry_types_org_id'), table_name='time_entry_types')
    op.drop_table('time_entry_types')
