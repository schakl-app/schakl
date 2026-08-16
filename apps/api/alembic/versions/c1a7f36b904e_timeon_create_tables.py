"""timeon: create the sync integration's four tables

Purely additive, which is what makes it safe under the rules ``docs/WORKFLOW.md`` sets for a
schema change that runs unattended on somebody else's production data:

- **Which released versions upgrade into this?** Any at or after ``e5c28a71b0d4``; nothing here
  reads or reshapes an existing column or table, so an older head chains straight in.
- **What happens to existing rows?** Nothing. No existing table is touched, and the integration
  is off until a tenant enables it and pastes a key.
- **Is it reversible?** Yes: ``downgrade`` drops the four new tables and nothing else. The time
  entries a sync may have written stay — they are records of work, not integration state.
- **Can the previous image run against the new schema?** Yes. The API rolls ``start-first``, so
  for the length of every deploy the old and new images both serve against this schema; the old
  one simply never selects these tables.

RLS is enabled and **forced** on all four, like every domain table (Golden Rule 1).

Revision ID: c1a7f36b904e
Revises: e5c28a71b0d4
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c1a7f36b904e"
down_revision = "b3f1c07d9a52"
branch_labels = None
depends_on = None

_TABLES = (
    "timeon_accounts",
    "timeon_links",
    "timeon_conflicts",
    "timeon_sync_runs",
)


def _rls(table: str) -> None:
    """Enable and force RLS with the org policy every domain table carries."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_org_isolation ON {table} "
        "USING (org_id = current_setting('app.current_org', true)::uuid) "
        "WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "timeon_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("base_url", sa.String(255), nullable=True),
        sa.Column("organisation_id", sa.Integer(), nullable=True),
        sa.Column("organisation_name", sa.String(255), nullable=True),
        sa.Column(
            "organisation_info",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        # Both directions start at `off`: connecting must never be the act that starts writing.
        sa.Column("hours_direction", sa.String(10), nullable=False, server_default="off"),
        sa.Column("projects_direction", sa.String(10), nullable=False, server_default="off"),
        sa.Column("conflict_policy", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="45"),
        sa.Column("history_floor", sa.Date(), nullable=True),
        sa.Column(
            "protect_invoiced", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "protect_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "push_approvals", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "create_missing_projects",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "create_missing_users", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("auto_sync", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_push_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("org_id", "name", name="uq_timeon_accounts_org_name"),
    )
    op.create_index("ix_timeon_accounts_org_active", "timeon_accounts", ["org_id", "active"])

    op.create_table(
        "timeon_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("timeon_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        # A real FK, because it is what the company horizon matches on (#285): a link with no
        # anchor would filter nothing at all for a restricted staff member.
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # No FK: it points across a module boundary (§6) at a time entry, a project, a company
        # or a user, and it is NULL for anything Timeon holds that schakl does not.
        sa.Column("local_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("external_name", sa.String(255), nullable=True),
        sa.Column("external_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("origin", sa.String(10), nullable=False, server_default="timeon"),
        # Two fingerprints, never one `synced` flag: comparing each against now is what answers
        # "which side moved", and only both moving is a conflict.
        sa.Column("local_hash", sa.String(64), nullable=True),
        sa.Column("remote_hash", sa.String(64), nullable=True),
        sa.Column(
            "observed",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=True),
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
            "org_id", "account_id", "kind", "external_id", name="uq_timeon_links_external"
        ),
    )
    op.create_index(
        "uq_timeon_links_local",
        "timeon_links",
        ["org_id", "account_id", "kind", "local_id"],
        unique=True,
        postgresql_where=sa.text("local_id IS NOT NULL"),
    )
    op.create_index(
        "ix_timeon_links_account_kind", "timeon_links", ["account_id", "kind", "status"]
    )
    op.create_index("ix_timeon_links_company", "timeon_links", ["org_id", "company_id"])
    op.create_index(
        "ix_timeon_links_window", "timeon_links", ["account_id", "kind", "external_date"]
    )

    op.create_table(
        "timeon_conflicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("timeon_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("timeon_links.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "differences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "local_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "remote_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.String(500), nullable=True),
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
    )
    op.create_index(
        "ix_timeon_conflicts_open",
        "timeon_conflicts",
        ["org_id", "account_id", "status", "detected_at"],
    )
    # One open conflict per pairing. A second detection updates the row rather than stacking a
    # duplicate, so the queue counts decisions rather than detections.
    op.create_index(
        "uq_timeon_conflicts_open_link",
        "timeon_conflicts",
        ["org_id", "link_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "timeon_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("timeon_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("window_from", sa.Date(), nullable=True),
        sa.Column("window_to", sa.Date(), nullable=True),
        sa.Column(
            "counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    )
    op.create_index(
        "ix_timeon_sync_runs_recent", "timeon_sync_runs", ["org_id", "account_id", "created_at"]
    )

    for table in _TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    op.drop_table("timeon_sync_runs")
    op.drop_table("timeon_conflicts")
    op.drop_table("timeon_links")
    op.drop_table("timeon_accounts")
