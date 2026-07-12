"""google_gmail_create_tables

Revision ID: c9e6ec23e591
Revises: e90705fca644
Create Date: 2026-07-12 00:00:00.000000

New google.gmail table (issue #22): the per-mailbox suppression list behind the owner's
"reject" — a rejected email must never be re-imported. Expand-only: additive DDL, nothing
else references it, older code never reads it — rollback (downgrade drops it + its RLS
policy) is safe from any released version.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'c9e6ec23e591'
down_revision: str | None = 'e90705fca644'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'gmail_suppressions',
        sa.Column('connection_id', sa.UUID(), nullable=False),
        sa.Column('gmail_message_id', sa.String(length=64), nullable=True),
        sa.Column('gmail_thread_id', sa.String(length=64), nullable=True),
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
            ['connection_id'], ['google_connections.id'],
            name=op.f('fk_gmail_suppressions_connection_id_google_connections'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_gmail_suppressions_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_gmail_suppressions')),
    )
    op.create_index(op.f('ix_gmail_suppressions_org_id'), 'gmail_suppressions', ['org_id'])
    op.create_index(
        op.f('ix_gmail_suppressions_connection_id'), 'gmail_suppressions', ['connection_id']
    )
    op.create_index(
        'uq_gmail_suppressions_org_conn_message',
        'gmail_suppressions',
        ['org_id', 'connection_id', 'gmail_message_id'],
        unique=True,
        postgresql_where=sa.text('gmail_message_id IS NOT NULL'),
    )
    op.create_index(
        'ix_gmail_suppressions_org_conn_thread',
        'gmail_suppressions',
        ['org_id', 'connection_id', 'gmail_thread_id'],
    )
    enable_rls('gmail_suppressions')


def downgrade() -> None:
    disable_rls('gmail_suppressions')
    op.drop_index('ix_gmail_suppressions_org_conn_thread', table_name='gmail_suppressions')
    op.drop_index('uq_gmail_suppressions_org_conn_message', table_name='gmail_suppressions')
    op.drop_index(
        op.f('ix_gmail_suppressions_connection_id'), table_name='gmail_suppressions'
    )
    op.drop_index(op.f('ix_gmail_suppressions_org_id'), table_name='gmail_suppressions')
    op.drop_table('gmail_suppressions')
