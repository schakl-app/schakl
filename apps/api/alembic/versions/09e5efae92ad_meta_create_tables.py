"""meta: create the integration's tables

Purely additive, which is what makes it safe under the rules ``docs/WORKFLOW.md`` sets for a
schema change that runs unattended on somebody else's production data:

- **Which released versions upgrade into this?** Any at or after ``b4e8d1a6c3f7``; nothing
  here reads or reshapes an existing column or table, so an older head chains straight in.
- **What happens to existing rows?** Nothing — there are none. Every table is new, and an
  instance that never enables the integration simply has six empty ones.
- **Is it reversible?** Yes: ``downgrade`` drops the six tables and their policies.
- **Can the previous image run against the new schema?** Yes. The API rolls ``start-first``,
  so for the length of every deploy the old and new images both serve against this schema;
  the old one never selects these tables.

RLS is enabled and **forced** on all six (Golden Rule 1), with the policy every domain table
here uses: rows are visible only while ``app.current_org`` matches.

Revision ID: 09e5efae92ad
Revises: b4e8d1a6c3f7
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "09e5efae92ad"
down_revision = "b4e8d1a6c3f7"
branch_labels = None
depends_on = None

_TABLES = (
    "meta_settings",
    "meta_credentials",
    "meta_assets",
    "meta_posts",
    "meta_post_targets",
    "meta_media_tokens",
)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_org_isolation ON {table} "
        "USING (org_id = current_setting('app.current_org', true)::uuid) "
        "WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)"
    )


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _stamp(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "org_id",
            _uuid(),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    ]


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "meta_settings",
        *_base(),
        sa.Column("app_id", sa.String(32), nullable=True),
        sa.Column("app_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("writes_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("facebook_scheduler", sa.String(16), nullable=False, server_default="schakl"),
        *_timestamps(),
        sa.UniqueConstraint("org_id", name="uq_meta_settings_org"),
    )

    op.create_table(
        "meta_credentials",
        *_base(),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("business_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("business_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("subject_id", sa.String(32), nullable=True),
        sa.Column("subject_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("token_kind", sa.String(32), nullable=True),
        sa.Column("token_app_id", sa.String(32), nullable=True),
        sa.Column("scopes", _jsonb(), nullable=False, server_default="[]"),
        _stamp("issued_at"),
        _stamp("expires_at"),
        _stamp("data_access_expires_at"),
        _stamp("refreshed_at"),
        _stamp("refresh_attempted_at"),
        sa.Column("refresh_error", sa.String(500), nullable=True),
        sa.Column("warned_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_error", sa.String(500), nullable=True),
        _stamp("last_verified_at"),
        _stamp("last_discovered_at"),
        *_timestamps(),
        sa.UniqueConstraint("org_id", "label", name="uq_meta_credentials_label"),
    )
    op.create_index("ix_meta_credentials_org_active", "meta_credentials", ["org_id", "active"])

    op.create_table(
        "meta_assets",
        *_base(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column(
            "credential_id",
            _uuid(),
            sa.ForeignKey("meta_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "company_id",
            _uuid(),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("picture_url", sa.Text(), nullable=True),
        sa.Column("linked_page_id", sa.String(64), nullable=True),
        sa.Column("relation", sa.String(16), nullable=False, server_default="owned"),
        sa.Column("tasks", _jsonb(), nullable=False, server_default="[]"),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("account_status", sa.Integer(), nullable=True),
        sa.Column("dsa_beneficiary", sa.String(512), nullable=True),
        sa.Column("dsa_payor", sa.String(512), nullable=True),
        _stamp("observed_at"),
        sa.Column("page_token_encrypted", sa.Text(), nullable=True),
        _stamp("page_token_at"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_error", sa.String(500), nullable=True),
        _stamp("last_verified_at"),
        *_timestamps(),
        sa.UniqueConstraint("org_id", "kind", "external_id", name="uq_meta_assets_identity"),
    )
    op.create_index("ix_meta_assets_org_company", "meta_assets", ["org_id", "company_id"])
    op.create_index("ix_meta_assets_org_kind", "meta_assets", ["org_id", "kind", "active"])

    op.create_table(
        "meta_posts",
        *_base(),
        sa.Column(
            "company_id",
            _uuid(),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("format", sa.String(16), nullable=False, server_default="post"),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("media", _jsonb(), nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        _stamp("scheduled_at"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        _stamp("published_at"),
        sa.Column(
            "created_by_user_id",
            _uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by_name", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "approved_by_user_id",
            _uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_by_name", sa.String(255), nullable=False, server_default=""),
        _stamp("approved_at"),
        *_timestamps(),
    )
    op.create_index("ix_meta_posts_org_status", "meta_posts", ["org_id", "status", "scheduled_at"])
    op.create_index("ix_meta_posts_org_company", "meta_posts", ["org_id", "company_id"])

    op.create_table(
        "meta_post_targets",
        *_base(),
        sa.Column(
            "post_id",
            _uuid(),
            sa.ForeignKey("meta_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            _uuid(),
            sa.ForeignKey("meta_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("body_override", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("scheduler", sa.String(16), nullable=False, server_default="schakl"),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("permalink", sa.Text(), nullable=True),
        sa.Column("container_id", sa.String(128), nullable=True),
        sa.Column("claim_id", _uuid(), nullable=True),
        _stamp("claimed_at"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        _stamp("published_at"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("last_error_code", sa.String(48), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("post_id", "asset_id", name="uq_meta_post_targets_asset"),
    )
    op.create_index("ix_meta_post_targets_org_status", "meta_post_targets", ["org_id", "status"])
    op.create_index("ix_meta_post_targets_post", "meta_post_targets", ["post_id"])

    op.create_table(
        "meta_media_tokens",
        *_base(),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column(
            "file_id", _uuid(), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "post_id",
            _uuid(),
            sa.ForeignKey("meta_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _stamp("expires_at", nullable=False),
        _stamp("revoked_at"),
        *_timestamps(),
        sa.UniqueConstraint("token", name="uq_meta_media_tokens_token"),
    )
    op.create_index("ix_meta_media_tokens_post", "meta_media_tokens", ["post_id"])

    for table in _TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.drop_table(table)
