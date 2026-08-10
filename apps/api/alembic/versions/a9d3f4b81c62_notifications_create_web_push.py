"""notifications_create_web_push

Browser push notifications as a fifth delivery channel (#309): two new tables, nothing else.

``push_subscriptions`` is one browser on one device. Deliberately not a ``notification_channels``
row — a push subscription is nothing a person types, and as a channel row an ordinary auto-prune
would delete a user's channel along with the preference rows carrying its routing.

``push_vapid_keys`` is the org's application-server identity to the push services (RFC 8292),
generated lazily on first use rather than configured, so the feature is not silently off after an
unattended upgrade.

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Additive only.** Two new tables; nothing existing is dropped, renamed, retyped or backfilled.
  It applies on top of any older head that has ``orgs`` and ``users``, which is every one of them.
* **No behaviour change on upgrade.** Both tables come up empty. The web-push channel writes no
  delivery rows until somebody grants notification permission in a browser, so an instance that
  upgrades into this and never opens the setting behaves exactly as before.
* **Rolling the image tag back is safe.** The previous release does not know these tables exist
  and reads nothing from them; they simply sit there. Rolling forward again finds the
  subscriptions still valid, because nothing rotates the VAPID keys.
* **Reversible.** ``downgrade`` drops both tables. That discards every registered device and the
  org keypair, so re-upgrading asks every user to grant permission again — an inconvenience, not
  a data loss: a subscription is a pointer to a browser, and the browser still has the notifications.

Revision ID: a9d3f4b81c62
Revises: a1c7e3f9d240
Create Date: 2026-08-10 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'a9d3f4b81c62'
down_revision: str | None = 'a1c7e3f9d240'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'push_subscriptions',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('p256dh', sa.String(length=160), nullable=False),
        sa.Column('auth', sa.String(length=40), nullable=False),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_push_subscriptions_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_push_subscriptions_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_push_subscriptions')),
        sa.UniqueConstraint('org_id', 'endpoint', name='uq_push_subscriptions_endpoint'),
    )
    op.create_index(op.f('ix_push_subscriptions_org_id'), 'push_subscriptions', ['org_id'], unique=False)
    op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'], unique=False)
    op.create_index('ix_push_subscriptions_user', 'push_subscriptions', ['org_id', 'user_id'], unique=False)

    op.create_table(
        'push_vapid_keys',
        sa.Column('public_key', sa.String(length=160), nullable=False),
        sa.Column('private_key_enc', sa.Text(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_push_vapid_keys_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_push_vapid_keys')),
        sa.UniqueConstraint('org_id', name='uq_push_vapid_keys_org'),
    )
    op.create_index(op.f('ix_push_vapid_keys_org_id'), 'push_vapid_keys', ['org_id'], unique=False)

    # Tenant isolation (defence-in-depth): both are org-scoped, RLS-forced (CLAUDE.md §5).
    enable_rls('push_subscriptions')
    enable_rls('push_vapid_keys')


def downgrade() -> None:
    disable_rls('push_vapid_keys')
    disable_rls('push_subscriptions')
    op.drop_index(op.f('ix_push_vapid_keys_org_id'), table_name='push_vapid_keys')
    op.drop_table('push_vapid_keys')
    op.drop_index('ix_push_subscriptions_user', table_name='push_subscriptions')
    op.drop_index(op.f('ix_push_subscriptions_user_id'), table_name='push_subscriptions')
    op.drop_index(op.f('ix_push_subscriptions_org_id'), table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
