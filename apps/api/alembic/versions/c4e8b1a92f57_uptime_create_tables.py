"""uptime_create_tables

Revision ID: c4e8b1a92f57
Revises: f3b6c81a9e27
Create Date: 2026-08-11 11:40:00.000000

New module tables (docs/UPTIME.md): Uptime Kuma instances and the monitor mirror.

Expand-only and safe to roll back from any released version: additive DDL, no backfill, nothing
existing references either table, and no older code reads them. ``websites.uptime_enabled`` is
deliberately **untouched** — it has existed since #94 as a flag with nothing behind it, and this
module is what finally acts on it, so the column keeps its meaning and its data.

Two things worth knowing before changing this migration:

* ``uptime_monitors.company_id`` is denormalised from the website → domain chain and nullable.
  ``NULL`` means *attached to no client*, which the tenant repository already reads as "not
  company data, stays visible" (#285). It is not a missing value to backfill.
* ``secret_salt`` and ``webhook_secret`` are ``NOT NULL`` with no server default on purpose:
  both are minted per row by the service. A shared default would make every instance's secret
  fingerprints comparable across tenants, which is the one property the per-instance salt exists
  to prevent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = "c4e8b1a92f57"
down_revision: str | None = "f3b6c81a9e27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "uptime_instances",
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
        sa.Column("mode", sa.String(16), nullable=False, server_default="managed"),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("token_encrypted", sa.Text(), nullable=True),
        sa.Column("connect_headers_encrypted", sa.Text(), nullable=True),
        sa.Column("ssl_verify", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("secret_salt", sa.String(64), nullable=False),
        sa.Column("webhook_secret", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("server_version", sa.String(40), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.UniqueConstraint("org_id", "name", name="uq_uptime_instances_org_name"),
    )
    op.create_index("ix_uptime_instances_org_mode", "uptime_instances", ["org_id", "mode"])
    enable_rls("uptime_instances")

    op.create_table(
        "uptime_monitors",
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
            "instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("uptime_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("monitor_type", sa.String(40), nullable=False),
        sa.Column("target", sa.String(1000), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("retries", sa.Integer(), nullable=True),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("uptime_monitors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "website_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("websites.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "domain_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("domains.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "hosting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hosting.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kuma_monitor_id", sa.Integer(), nullable=True),
        sa.Column(
            "remote_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String(500), nullable=True),
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
        sa.UniqueConstraint(
            "org_id", "instance_id", "kuma_monitor_id", name="uq_uptime_monitors_instance_kuma"
        ),
    )
    op.create_index(
        "ix_uptime_monitors_org_instance", "uptime_monitors", ["org_id", "instance_id"]
    )
    op.create_index("ix_uptime_monitors_org_company", "uptime_monitors", ["org_id", "company_id"])
    op.create_index("ix_uptime_monitors_org_website", "uptime_monitors", ["org_id", "website_id"])
    op.create_index("ix_uptime_monitors_org_sync", "uptime_monitors", ["org_id", "sync_status"])
    enable_rls("uptime_monitors")


def downgrade() -> None:
    disable_rls("uptime_monitors")
    op.drop_index("ix_uptime_monitors_org_sync", table_name="uptime_monitors")
    op.drop_index("ix_uptime_monitors_org_website", table_name="uptime_monitors")
    op.drop_index("ix_uptime_monitors_org_company", table_name="uptime_monitors")
    op.drop_index("ix_uptime_monitors_org_instance", table_name="uptime_monitors")
    op.drop_table("uptime_monitors")

    disable_rls("uptime_instances")
    op.drop_index("ix_uptime_instances_org_mode", table_name="uptime_instances")
    op.drop_table("uptime_instances")
