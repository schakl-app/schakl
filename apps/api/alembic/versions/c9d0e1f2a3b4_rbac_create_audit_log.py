"""rbac_create_audit_log

An org-scoped, RLS-forced trail of role changes (issue #19). Today a member invite only reaches
``logger.info`` and a role change writes nothing at all, so "who gave them that?" is unanswerable.

This is deliberately **not** the instance-level ``instance_audit_log``: granting someone
``settings.roles.manage`` is a within-tenant action and an agency must be able to read its own
history. Same shape as ``task_activities`` — an ``action`` key plus a JSONB payload.

``role_id`` is a plain column, not a foreign key: the trail has to survive the role it describes,
so a deleted role leaves its id and a name snapshot behind rather than cascading its history away.

Upgrade path: one new table. Purely additive, safe on a populated database, and a rolled-back
image simply ignores it.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-10 13:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: str | None = 'b8c9d0e1f2a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'role_audit_log',
        sa.Column('actor_user_id', sa.UUID(), nullable=True),
        sa.Column('actor_email', sa.String(length=320), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('role_id', sa.UUID(), nullable=True),
        sa.Column('role_key', sa.String(length=64), nullable=True),
        sa.Column('target_user_id', sa.UUID(), nullable=True),
        sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_role_audit_log_actor_user_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_role_audit_log_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], name=op.f('fk_role_audit_log_target_user_id_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_role_audit_log')),
    )
    op.create_index(op.f('ix_role_audit_log_org_id'), 'role_audit_log', ['org_id'], unique=False)
    op.create_index(
        'ix_role_audit_log_org_id_created_at', 'role_audit_log', ['org_id', 'created_at'], unique=False
    )
    enable_rls("role_audit_log")


def downgrade() -> None:
    disable_rls("role_audit_log")
    op.drop_index('ix_role_audit_log_org_id_created_at', table_name='role_audit_log')
    op.drop_index(op.f('ix_role_audit_log_org_id'), table_name='role_audit_log')
    op.drop_table('role_audit_log')
