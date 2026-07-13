"""core_add_nav_prefs

Issue #169: per-person and per-tenant sidebar navigation customization — DashboardPref's
shape exactly: one row per user plus at most one ``user_id IS NULL`` org-default row,
``items`` an ordered JSONB list of ``{key, hidden}`` entries.

Upgrade path: purely additive — one new table. A rolled-back image ignores it. Safe to run
unattended on a populated database.

Revision ID: 7676a29a560e
Revises: d674712e0ad7
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = '7676a29a560e'
down_revision: str | None = 'd674712e0ad7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'nav_prefs',
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('items', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='[]', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_nav_prefs_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name=op.f('fk_nav_prefs_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_nav_prefs')),
        sa.UniqueConstraint('org_id', 'user_id', name=op.f('uq_nav_prefs_org_id')),
    )
    op.create_index(op.f('ix_nav_prefs_org_id'), 'nav_prefs', ['org_id'], unique=False)
    # Postgres treats NULLs as distinct, so the org-default row needs its own partial guard.
    op.create_index('uq_nav_prefs_org_default', 'nav_prefs', ['org_id'], unique=True,
                    postgresql_where=sa.text('user_id IS NULL'))
    # Tenant isolation at the database layer too (Golden Rule 1).
    enable_rls('nav_prefs')


def downgrade() -> None:
    disable_rls('nav_prefs')
    op.drop_index('uq_nav_prefs_org_default', table_name='nav_prefs')
    op.drop_index(op.f('ix_nav_prefs_org_id'), table_name='nav_prefs')
    op.drop_table('nav_prefs')
