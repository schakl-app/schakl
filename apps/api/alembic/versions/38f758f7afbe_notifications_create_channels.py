"""notifications_create_channels

External notification channels via Apprise (#17): a per-org (and per-user) configured transport
whose Apprise URL is encrypted at rest, plus a ``channel_config_id`` on ``notification_deliveries``
so a delivery attempt knows which channel it targets.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Additive.** One new RLS-forced table and one nullable FK column on an existing table (its rows
  were never written before #17, so nothing to backfill). Applies on top of any released ``head``.
* **Rollback-safe.** The previous image never selects ``notification_channels`` or the new column.
* No data seeding, so no per-org GUC dance. The new permission reaches existing orgs via the
  startup reconciler (a migration must never import the catalog).

Revision ID: 38f758f7afbe
Revises: aef949b553f1
Create Date: 2026-07-11 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = '38f758f7afbe'
down_revision: str | None = 'aef949b553f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'notification_channels',
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('url_enc', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('event_filter', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_notification_channels_created_by_user_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_notification_channels_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notification_channels_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_channels')),
    )
    op.create_index(op.f('ix_notification_channels_org_id'), 'notification_channels', ['org_id'], unique=False)
    op.create_index(op.f('ix_notification_channels_user_id'), 'notification_channels', ['user_id'], unique=False)

    op.add_column(
        'notification_deliveries',
        sa.Column('channel_config_id', sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f('fk_notification_deliveries_channel_config_id_notification_channels'),
        'notification_deliveries',
        'notification_channels',
        ['channel_config_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.create_index(
        op.f('ix_notification_deliveries_channel_config_id'),
        'notification_deliveries',
        ['channel_config_id'],
        unique=False,
    )

    enable_rls('notification_channels')


def downgrade() -> None:
    disable_rls('notification_channels')
    op.drop_index(op.f('ix_notification_deliveries_channel_config_id'), table_name='notification_deliveries')
    op.drop_constraint(
        op.f('fk_notification_deliveries_channel_config_id_notification_channels'),
        'notification_deliveries',
        type_='foreignkey',
    )
    op.drop_column('notification_deliveries', 'channel_config_id')
    op.drop_index(op.f('ix_notification_channels_user_id'), table_name='notification_channels')
    op.drop_index(op.f('ix_notification_channels_org_id'), table_name='notification_channels')
    op.drop_table('notification_channels')
