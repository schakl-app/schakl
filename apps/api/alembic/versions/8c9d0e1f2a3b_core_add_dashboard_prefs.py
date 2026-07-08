"""core_add_dashboard_prefs

Per-user My Day layout (ordered widget keys) plus one org-default template row
(``user_id IS NULL``). Org-scoped + RLS-forced.

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
Create Date: 2026-07-08 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = '8c9d0e1f2a3b'
down_revision: str | None = '7b8c9d0e1f2a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'dashboard_prefs',
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('widgets', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_dashboard_prefs_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_dashboard_prefs_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_dashboard_prefs')),
        sa.UniqueConstraint('org_id', 'user_id', name=op.f('uq_dashboard_prefs_org_id')),
    )
    op.create_index(op.f('ix_dashboard_prefs_org_id'), 'dashboard_prefs', ['org_id'], unique=False)
    op.create_index(
        'uq_dashboard_prefs_org_default',
        'dashboard_prefs',
        ['org_id'],
        unique=True,
        postgresql_where=sa.text('user_id IS NULL'),
    )

    enable_rls("dashboard_prefs")


def downgrade() -> None:
    disable_rls("dashboard_prefs")

    op.drop_index('uq_dashboard_prefs_org_default', table_name='dashboard_prefs')
    op.drop_index(op.f('ix_dashboard_prefs_org_id'), table_name='dashboard_prefs')
    op.drop_table('dashboard_prefs')
