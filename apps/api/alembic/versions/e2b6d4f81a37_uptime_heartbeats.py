"""uptime_heartbeats

Revision ID: e2b6d4f81a37
Revises: d7a3c5e19b62
Create Date: 2026-08-11 13:30:00.000000

Gate 3 of docs/UPTIME.md: the bounded rolling window a reported heartbeat lands in.

Expand-only, one new table, no backfill. Uptime Kuma keeps the real history and answers
questions about it better than a mirror would, so this holds only what a panel and a report
section draw, pruned by cron.

The unique constraint is the load-bearing part. It is **the** idempotency guarantee, at the
database rather than in application code: a monitor flapping delivers the same transition twice
and Kuma retries besides, so "have we recorded this?" followed by an insert leaves a window
every retry enters — including across two API replicas that share no memory. The insert is
``ON CONFLICT DO NOTHING`` against this constraint, which makes the duplicate *impossible*
rather than unlikely.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = "e2b6d4f81a37"
down_revision: str | None = "d7a3c5e19b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "uptime_heartbeats",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "monitor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("uptime_monitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("ping_ms", sa.Integer(), nullable=True),
        sa.Column("reported", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint(
            "org_id", "monitor_id", "status", "observed_at", name="uq_uptime_heartbeat_event"
        ),
    )
    op.create_index(
        "ix_uptime_heartbeats_org_monitor_time",
        "uptime_heartbeats",
        ["org_id", "monitor_id", "observed_at"],
    )
    enable_rls("uptime_heartbeats")


def downgrade() -> None:
    disable_rls("uptime_heartbeats")
    op.drop_index("ix_uptime_heartbeats_org_monitor_time", table_name="uptime_heartbeats")
    op.drop_table("uptime_heartbeats")
