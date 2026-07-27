"""notifications_add_channel_cadence

Digest cadence on an external channel (#283, Phase A). Until now only *personal e-mail* could
batch: ``dispatch_email_deliveries`` held a recipient's rows until their slot and sent one mail.
Every Apprise transport (Slack, Teams, Discord, a webhook) fired one message per event, because
a channel had nowhere to record a cadence. These three columns mirror the ones
``notification_preferences`` already carries, so ``compute_visible_at`` places a channel's
deliveries exactly the way it places a person's.

The pending-delivery index is widened at the same time: the new sweep groups by
``channel_config_id`` and filters on ``channel`` + ``deliver_after``, which the old
``(org_id, created_at)`` shape could not serve.

Upgrade plan (docs/WORKFLOW.md -> *Breaking database changes*):

* **Expand-only.** Three columns; ``digest`` lands ``NOT NULL DEFAULT 'immediate'`` so every
  existing channel keeps today's behaviour (one message per event, sent on the next tick) with
  no backfill. Applies on top of any released ``head``.
* **Rollback-safe.** The previous image never reads the columns; the index is only an index.
  Rolled back, pending rows carrying a future ``deliver_after`` are simply sent one-by-one
  immediately by the old per-row dispatcher -- degraded, not broken.

Revision ID: d4a1c7e93b28
Revises: f4d6b81e37ac
Create Date: 2026-07-27 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a1c7e93b28'
down_revision: str | None = 'f4d6b81e37ac'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'notification_channels',
        sa.Column(
            'digest',
            sa.String(length=10),
            nullable=False,
            server_default='immediate',
        ),
    )
    op.add_column(
        'notification_channels',
        sa.Column('digest_time', sa.Time(), nullable=True),
    )
    op.add_column(
        'notification_channels',
        sa.Column('digest_weekday', sa.Integer(), nullable=True),
    )

    # The sweep now asks "which pending rows for this channel are due?" -- the old index
    # answered "which pending rows for this org are oldest?".
    op.drop_index('ix_notification_deliveries_pending', table_name='notification_deliveries')
    op.create_index(
        'ix_notification_deliveries_pending',
        'notification_deliveries',
        ['org_id', 'channel', 'deliver_after', 'created_at'],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index('ix_notification_deliveries_pending', table_name='notification_deliveries')
    op.create_index(
        'ix_notification_deliveries_pending',
        'notification_deliveries',
        ['org_id', 'created_at'],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_column('notification_channels', 'digest_weekday')
    op.drop_column('notification_channels', 'digest_time')
    op.drop_column('notification_channels', 'digest')
