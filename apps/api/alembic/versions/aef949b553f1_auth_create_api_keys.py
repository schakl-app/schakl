"""auth_create_api_keys

API keys and service accounts (#20): personal keys that act as their owner, and service-account
keys that act as a synthetic, employee-outliving principal. Both org-scoped and RLS-forced.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive.** Two new tables, no changes to existing ones. Applies on top of any
  released ``head``.
* **Rollback-safe.** The previous image never selects ``api_keys`` or ``service_accounts``; the
  API-key auth path is new code.
* No data seeding, so no per-org GUC dance. Permissions for the new capability reach existing
  orgs through the startup reconciler (a migration must never import the catalog).

Revision ID: aef949b553f1
Revises: c812f69d84d6
Create Date: 2026-07-11 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'aef949b553f1'
down_revision: str | None = 'c812f69d84d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'service_accounts',
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_service_accounts_created_by_user_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_service_accounts_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_service_accounts')),
    )
    op.create_index(op.f('ix_service_accounts_org_id'), 'service_accounts', ['org_id'], unique=False)

    op.create_table(
        'api_keys',
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('prefix', sa.String(length=32), nullable=False),
        sa.Column('hash', sa.String(length=64), nullable=False),
        sa.Column('principal_type', sa.String(length=20), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('service_account_id', sa.UUID(), nullable=True),
        sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_api_keys_created_by_user_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_api_keys_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['service_account_id'], ['service_accounts.id'], name=op.f('fk_api_keys_service_account_id_service_accounts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_api_keys_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_api_keys')),
        sa.UniqueConstraint('org_id', 'prefix', name='uq_api_keys_org_id_prefix'),
    )
    op.create_index(op.f('ix_api_keys_org_id'), 'api_keys', ['org_id'], unique=False)
    op.create_index(op.f('ix_api_keys_prefix'), 'api_keys', ['prefix'], unique=False)
    op.create_index(op.f('ix_api_keys_user_id'), 'api_keys', ['user_id'], unique=False)
    op.create_index(op.f('ix_api_keys_service_account_id'), 'api_keys', ['service_account_id'], unique=False)

    enable_rls('service_accounts')
    enable_rls('api_keys')


def downgrade() -> None:
    disable_rls('api_keys')
    disable_rls('service_accounts')
    op.drop_index(op.f('ix_api_keys_service_account_id'), table_name='api_keys')
    op.drop_index(op.f('ix_api_keys_user_id'), table_name='api_keys')
    op.drop_index(op.f('ix_api_keys_prefix'), table_name='api_keys')
    op.drop_index(op.f('ix_api_keys_org_id'), table_name='api_keys')
    op.drop_table('api_keys')
    op.drop_index(op.f('ix_service_accounts_org_id'), table_name='service_accounts')
    op.drop_table('service_accounts')
