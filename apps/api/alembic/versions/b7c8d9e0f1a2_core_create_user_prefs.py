"""core_create_user_prefs

Per-user personal preferences: a JSONB blob keyed by feature namespace (e.g. the timesheet
week view). One row per (org, user). Org-scoped + RLS-forced. Additive DDL only.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-08 10:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: str | None = 'a6b7c8d9e0f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'user_prefs',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('prefs', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_user_prefs_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_prefs_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_user_prefs')),
        sa.UniqueConstraint('org_id', 'user_id', name=op.f('uq_user_prefs_org_id')),
    )
    op.create_index(op.f('ix_user_prefs_org_id'), 'user_prefs', ['org_id'], unique=False)
    op.create_index(op.f('ix_user_prefs_user_id'), 'user_prefs', ['user_id'], unique=False)

    enable_rls("user_prefs")


def downgrade() -> None:
    disable_rls("user_prefs")

    op.drop_index(op.f('ix_user_prefs_user_id'), table_name='user_prefs')
    op.drop_index(op.f('ix_user_prefs_org_id'), table_name='user_prefs')
    op.drop_table('user_prefs')
