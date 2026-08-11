"""uptime_profiles_and_drift

Revision ID: d7a3c5e19b62
Revises: c4e8b1a92f57
Create Date: 2026-08-11 12:40:00.000000

Gate 2 of docs/UPTIME.md: tenant-defined monitor defaults, and the columns drift needs.

Expand-only and safe to roll back: one new table, three nullable columns, no backfill. An
existing row with ``profile_id IS NULL`` follows the tenant's default profile — which is
resolution, not a missing value, so nothing here needs filling in.

``drift_fields`` is a JSONB array rather than a boolean because "this monitor disagrees with
Uptime Kuma" is not actionable and "its interval and its URL disagree" is. The screen prints
what moved; a boolean would send somebody to compare a hundred fields by eye.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = "d7a3c5e19b62"
down_revision: str | None = "c4e8b1a92f57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "uptime_monitor_profiles",
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
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("monitor_type", sa.String(40), nullable=False, server_default="http"),
        # The tenant's own defaults, as the same keys a monitor row uses. JSONB rather than
        # columns because what a profile may carry grows with each monitor type Kuma adds, and
        # a migration per option is how a defaults table becomes something nobody extends.
        sa.Column(
            "defaults",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        # Uptime Kuma's own notification channel ids. We assign monitors to them and do not
        # manage them: an agency configuring Slack in Kuma is doing the right thing.
        sa.Column(
            "notification_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_uptime_profiles_org_name"),
    )
    op.create_index(
        "ix_uptime_profiles_org_type", "uptime_monitor_profiles", ["org_id", "monitor_type"]
    )
    enable_rls("uptime_monitor_profiles")

    op.add_column(
        "uptime_monitors",
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("uptime_monitor_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Which of *our* fields Uptime Kuma disagrees with, as of the last sync.
    op.add_column(
        "uptime_monitors",
        sa.Column(
            "drift_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    # Whether schakl created this monitor, or found it. It decides what a *first* sync means:
    # a monitor we adopted has no intent of its own to disagree with yet, so its observed state
    # is simply the truth — while one we created and pushed does, and a difference is drift.
    op.add_column(
        "uptime_monitors",
        sa.Column("adopted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("uptime_monitors", "adopted")
    op.drop_column("uptime_monitors", "drift_fields")
    op.drop_column("uptime_monitors", "profile_id")
    disable_rls("uptime_monitor_profiles")
    op.drop_index("ix_uptime_profiles_org_type", table_name="uptime_monitor_profiles")
    op.drop_table("uptime_monitor_profiles")
