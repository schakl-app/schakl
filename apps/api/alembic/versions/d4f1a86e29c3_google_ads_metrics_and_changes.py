"""google_ads_metrics_and_changes

Revision ID: d4f1a86e29c3
Revises: c8a3f61b7e42
Create Date: 2026-08-11 14:00:00.000000

The nightly mirror's two tables, plus the sync's own error column.

Purely additive: a new module's own tables and one nullable column, so an existing install
upgrades unattended and a rollback drops exactly what this created.

Two constraints are load-bearing rather than incidental, and both are about **re-running**. The
sync re-pulls a trailing window every night and upserts, because Ads conversions keep arriving
for days after the click and a day read once is a day read too early — so each table needs a key
that says what a row *is*:

* ``uq_google_ads_metrics_daily_row`` is ``(org, account, date, dimension, dim_key)``, and
  ``dim_key`` is ``NOT NULL DEFAULT ''`` rather than nullable for exactly that reason: Postgres
  treats NULLs as distinct inside a unique constraint, so a nullable key column would let the
  account-wide row be stored again every night and the upsert would silently be an insert.
* ``uq_google_ads_changes_event`` identifies a change by ``(instant, resource, operation)``
  because **Google gives change events no id at all**. Anything less specific would collapse two
  real edits; anything more (the changed fields) would re-insert the same event whenever Google
  filled its own history in a little more.

``google_ads_accounts.last_sync_error`` is separate from ``last_error`` on purpose: verify and
sync ask Google different questions, and a nightly sync failing against a credential that
verifies perfectly is precisely the state one shared column would hide.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = "d4f1a86e29c3"
down_revision: str | None = "c8a3f61b7e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("google_ads_changes", "google_ads_metrics_daily")


def upgrade() -> None:
    op.add_column(
        "google_ads_accounts", sa.Column("last_sync_error", sa.String(length=500), nullable=True)
    )

    op.create_table(
        "google_ads_metrics_daily",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("dim_key", sa.String(length=64), server_default="", nullable=False),
        sa.Column("label", sa.String(length=255), server_default="", nullable=False),
        sa.Column(
            "metrics", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        # CASCADE, unlike everything hanging off a *client*: these rows describe the account and
        # mean nothing without it, and unlinking is a deactivation rather than a delete anyway.
        sa.ForeignKeyConstraint(
            ["account_id"], ["google_ads_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "account_id", "date", "dimension", "dim_key",
            name="uq_google_ads_metrics_daily_row",
        ),
    )
    op.create_index(
        op.f("ix_google_ads_metrics_daily_org_id"), "google_ads_metrics_daily", ["org_id"]
    )
    op.create_index(
        "ix_google_ads_metrics_daily_lookup",
        "google_ads_metrics_daily",
        ["org_id", "account_id", "dimension", "date"],
    )

    op.create_table(
        "google_ads_changes",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resource_type", sa.String(length=64), server_default="", nullable=False),
        sa.Column("operation", sa.String(length=16), server_default="", nullable=False),
        sa.Column("changed_resource", sa.String(length=512), server_default="", nullable=False),
        sa.Column("campaign", sa.String(length=512), nullable=True),
        sa.Column("ad_group", sa.String(length=512), nullable=True),
        sa.Column("changed_by", sa.String(length=320), nullable=True),
        sa.Column("client_type", sa.String(length=64), nullable=True),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["google_ads_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "account_id", "changed_at", "changed_resource", "operation",
            name="uq_google_ads_changes_event",
        ),
    )
    op.create_index(op.f("ix_google_ads_changes_org_id"), "google_ads_changes", ["org_id"])
    op.create_index(
        "ix_google_ads_changes_account_at",
        "google_ads_changes",
        ["org_id", "account_id", "changed_at"],
    )

    for table in _TABLES:
        enable_rls(table)


def downgrade() -> None:
    for table in _TABLES:
        disable_rls(table)
    op.drop_index("ix_google_ads_changes_account_at", table_name="google_ads_changes")
    op.drop_index(op.f("ix_google_ads_changes_org_id"), table_name="google_ads_changes")
    op.drop_table("google_ads_changes")
    op.drop_index("ix_google_ads_metrics_daily_lookup", table_name="google_ads_metrics_daily")
    op.drop_index(
        op.f("ix_google_ads_metrics_daily_org_id"), table_name="google_ads_metrics_daily"
    )
    op.drop_table("google_ads_metrics_daily")
    op.drop_column("google_ads_accounts", "last_sync_error")
