"""meta_ads: create the integration's tables

Purely additive (``docs/WORKFLOW.md``):

- **Which released versions upgrade into this?** Any at or after ``09e5efae92ad``, which is
  the migration that creates ``meta_assets`` — the table two of these point at.
- **What happens to existing rows?** Nothing — every table is new.
- **Is it reversible?** Yes: ``downgrade`` drops the three tables and their policies.
- **Can the previous image run against the new schema?** Yes; it never selects these tables.

RLS is enabled and **forced** on all three (Golden Rule 1).

The house policy is the row whose ``asset_id IS NULL``. NULLs are distinct inside a unique
constraint, so ``UNIQUE (org_id, asset_id)`` alone would permit two of them; the partial
unique index is what makes "the house policy" one row per org.

Revision ID: c2a35d7a62c8
Revises: 09e5efae92ad
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c2a35d7a62c8"
down_revision = "09e5efae92ad"
branch_labels = None
depends_on = None

_TABLES = ("meta_ads_settings", "meta_ads_policies", "meta_ads_decisions")


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
        "meta_ads_settings",
        *_base(),
        sa.Column("writes_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_timestamps(),
        sa.UniqueConstraint("org_id", name="uq_meta_ads_settings_org"),
    )

    op.create_table(
        "meta_ads_policies",
        *_base(),
        sa.Column(
            "asset_id",
            _uuid(),
            sa.ForeignKey("meta_assets.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("max_daily_budget", sa.Integer(), nullable=True),
        sa.Column("max_lifetime_budget", sa.Integer(), nullable=True),
        sa.Column("max_budget_increase", sa.Numeric(6, 2), nullable=True),
        sa.Column("banned_phrases", _jsonb(), nullable=False, server_default="[]"),
        sa.Column("dsa_beneficiary", sa.String(512), nullable=True),
        sa.Column("dsa_payor", sa.String(512), nullable=True),
        sa.Column("steering", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
        sa.UniqueConstraint("org_id", "asset_id", name="uq_meta_ads_policies_asset"),
    )
    op.create_index(
        "uq_meta_ads_policies_house",
        "meta_ads_policies",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("asset_id IS NULL"),
    )

    op.create_table(
        "meta_ads_decisions",
        *_base(),
        sa.Column(
            "asset_id",
            _uuid(),
            sa.ForeignKey("meta_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(24), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("subject_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("payload", _jsonb(), nullable=False, server_default="{}"),
        sa.Column(
            "decided_by_user_id",
            _uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_by_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("impersonator_name", sa.String(255), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_meta_ads_decisions_recent", "meta_ads_decisions", ["org_id", "asset_id", "created_at"]
    )
    op.create_index(
        "ix_meta_ads_decisions_subject",
        "meta_ads_decisions",
        ["org_id", "asset_id", "subject_id", "created_at"],
    )

    for table in _TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.drop_table(table)
