"""google_tag_manager: create the integration's tables

Purely additive, which is what makes it safe under the rules ``docs/WORKFLOW.md`` sets for a
schema change that runs unattended on somebody else's production data:

- **Which released versions upgrade into this?** Any at or after ``b8c1e40d7f52``; nothing here
  reads or reshapes an existing column or table, so an older head chains straight in.
- **What happens to existing rows?** Nothing — there are none. Every table is new, and an
  instance that never enables the integration simply has three empty ones.
- **Is it reversible?** Yes: ``downgrade`` drops the three tables and their policies.
- **Can the previous image run against the new schema?** Yes. The API rolls ``start-first``, so
  for the length of every deploy the old and new images both serve against this schema; the old
  one never selects these tables.

RLS is enabled and **forced** on all three (Golden Rule 1), with the policy every domain table
here uses: rows are visible only while ``app.current_org`` matches.

Revision ID: c3d9f4a17b62
Revises: b8c1e40d7f52
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c3d9f4a17b62"
down_revision = "b8c1e40d7f52"
branch_labels = None
depends_on = None

_TABLES = ("gtm_settings", "gtm_containers", "gtm_conversions")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_org_isolation ON {table} "
        "USING (org_id = current_setting('app.current_org', true)::uuid) "
        "WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)"
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "gtm_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("writes_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("own_workspace", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("workspace_name", sa.String(120), nullable=False, server_default="schakl"),
        *_timestamps(),
        sa.UniqueConstraint("org_id", name="uq_gtm_settings_org"),
    )

    op.create_table(
        "gtm_containers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("container_id", sa.String(32), nullable=False),
        sa.Column("public_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("path", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "website_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("websites.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("google_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "usage_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "domain_names",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "tagging_server_urls",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("live_version_id", sa.String(32), nullable=True),
        sa.Column("live_version_name", sa.String(255), nullable=True),
        sa.Column("tag_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trigger_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("variable_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("workspace_changes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("org_id", "container_id", name="uq_gtm_containers_org_container"),
    )
    op.create_index("ix_gtm_containers_org_company", "gtm_containers", ["org_id", "company_id"])
    op.create_index("ix_gtm_containers_org_active", "gtm_containers", ["org_id", "active"])

    op.create_table(
        "gtm_conversions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "container_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gtm_containers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("workspace_id", sa.String(32), nullable=True),
        sa.Column("trigger_id", sa.String(32), nullable=True),
        sa.Column("tag_id", sa.String(32), nullable=True),
        sa.Column("published_version_id", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by_name", sa.String(255), nullable=False, server_default=""),
        *_timestamps(),
        sa.UniqueConstraint("org_id", "container_id", "key", name="uq_gtm_conversions_key"),
    )
    op.create_index(
        "ix_gtm_conversions_container", "gtm_conversions", ["org_id", "container_id", "status"]
    )

    for table in _TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.drop_table(table)
