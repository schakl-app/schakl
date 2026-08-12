"""oauth_create_clients_and_grants

Revision ID: c4e71a9d2b58
Revises: a2f9d1c7b364
Create Date: 2026-08-12 09:00:00.000000

OAuth 2.1 for the MCP surface (docs/MCP.md, CLAUDE.md §12): two tables and three columns.

What is *not* here is the shape of the feature. There is **no access-token table** — the flow
issues an ordinary ``api_keys`` row, so tenant scoping, per-key scopes, revocation and the
live-permission cap are the ones already written and tested. The three columns added to
``api_keys`` are the only things the protocol needs that a key had no opinion about: which
client holds it, and the refresh secret that renews it without a person in the loop.

Purely **additive** — two new tables, three nullable columns — so an existing install upgrades
unattended and a rollback drops only what this created. docs/WORKFLOW.md's expand/contract rule
has nothing to contract here, and an instance that never enables OAuth carries three NULLs.

Both tables are org-scoped and RLS-forced like every domain table (Golden Rule 1). Registration
is unauthenticated, which is what RFC 7591 is for — but it is never *tenant*-less: the hostname
resolves the org before a row exists, so a client registered on one tenant's host is that
tenant's and is simply not found on another's.

Two constraints are the design rather than bookkeeping:

* ``uq_oauth_grants_org_id_code_hash`` is what makes an authorization code addressable at all
  (only its SHA-256 is stored), and it is per-org for the same reason every other unique index
  here is.
* single use is enforced by the **conditional update** in the service, not by this schema —
  ``UPDATE … WHERE redeemed_at IS NULL RETURNING`` — because two token requests racing across
  two API replicas is exactly the shape docs/PAYMENTS.md already lost once to an
  application-level check. ``ix_oauth_grants_expires_at`` exists so the pruning cron can find
  spent codes without a sequential scan.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'c4e71a9d2b58'
down_revision: str | None = 'a2f9d1c7b364'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'oauth_clients',
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('secret_hash', sa.String(length=64), nullable=True),
        sa.Column('client_name', sa.String(length=200), nullable=False),
        sa.Column('client_uri', sa.String(length=1024), nullable=True),
        sa.Column('logo_uri', sa.String(length=1024), nullable=True),
        sa.Column('redirect_uris', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_oauth_clients_created_by_user_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_oauth_clients_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_clients')),
        sa.UniqueConstraint('org_id', 'client_id', name='uq_oauth_clients_org_id_client_id'),
    )
    op.create_index(op.f('ix_oauth_clients_org_id'), 'oauth_clients', ['org_id'], unique=False)
    op.create_index(op.f('ix_oauth_clients_client_id'), 'oauth_clients', ['client_id'], unique=False)
    enable_rls('oauth_clients')

    op.create_table(
        'oauth_grants',
        sa.Column('client_pk', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('redirect_uri', sa.String(length=1024), nullable=False),
        sa.Column('code_challenge', sa.String(length=128), nullable=False),
        sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('resource', sa.String(length=1024), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('redeemed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('api_key_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], name=op.f('fk_oauth_grants_api_key_id_api_keys'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['client_pk'], ['oauth_clients.id'], name=op.f('fk_oauth_grants_client_pk_oauth_clients'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_oauth_grants_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_oauth_grants_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_grants')),
        sa.UniqueConstraint('org_id', 'code_hash', name='uq_oauth_grants_org_id_code_hash'),
    )
    op.create_index(op.f('ix_oauth_grants_org_id'), 'oauth_grants', ['org_id'], unique=False)
    op.create_index(op.f('ix_oauth_grants_client_pk'), 'oauth_grants', ['client_pk'], unique=False)
    op.create_index('ix_oauth_grants_expires_at', 'oauth_grants', ['expires_at'], unique=False)
    enable_rls('oauth_grants')

    # The key *is* the token (see the module docstring). Nullable throughout: every key minted
    # before this migration, and every one minted by hand afterwards, has no client and no
    # refresh secret — which is the honest representation of "this was not an OAuth session".
    op.add_column('api_keys', sa.Column('oauth_client_id', sa.UUID(), nullable=True))
    op.add_column('api_keys', sa.Column('refresh_prefix', sa.String(length=32), nullable=True))
    op.add_column('api_keys', sa.Column('refresh_hash', sa.String(length=64), nullable=True))
    op.add_column('api_keys', sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f('fk_api_keys_oauth_client_id_oauth_clients'),
        'api_keys',
        'oauth_clients',
        ['oauth_client_id'],
        ['id'],
        ondelete='CASCADE',
    )
    # Looked up on every refresh, exactly as ``prefix`` is on every request.
    op.create_index(op.f('ix_api_keys_refresh_prefix'), 'api_keys', ['refresh_prefix'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_api_keys_refresh_prefix'), table_name='api_keys')
    op.drop_constraint(op.f('fk_api_keys_oauth_client_id_oauth_clients'), 'api_keys', type_='foreignkey')
    op.drop_column('api_keys', 'refresh_expires_at')
    op.drop_column('api_keys', 'refresh_hash')
    op.drop_column('api_keys', 'refresh_prefix')
    op.drop_column('api_keys', 'oauth_client_id')

    disable_rls('oauth_grants')
    op.drop_index('ix_oauth_grants_expires_at', table_name='oauth_grants')
    op.drop_index(op.f('ix_oauth_grants_client_pk'), table_name='oauth_grants')
    op.drop_index(op.f('ix_oauth_grants_org_id'), table_name='oauth_grants')
    op.drop_table('oauth_grants')

    disable_rls('oauth_clients')
    op.drop_index(op.f('ix_oauth_clients_client_id'), table_name='oauth_clients')
    op.drop_index(op.f('ix_oauth_clients_org_id'), table_name='oauth_clients')
    op.drop_table('oauth_clients')
