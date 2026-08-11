"""google_ads_policy_and_decisions

Revision ID: f1c48d20a973
Revises: d4f1a86e29c3
Create Date: 2026-08-11 17:20:00.000000

The standing rules an agent reasons inside, and the log of what has already been settled.

Purely additive: two new tables belonging to one module, so an existing install upgrades
unattended and ``downgrade()`` drops exactly what this created. No backfill, and deliberately no
seeded rows — an org with no policy resolves to the built-in defaults, which is the posture an
upgrade has to land in.

Two constraints are load-bearing.

``uq_google_ads_policies_account`` is the ordinary one, and it does **not** cover the org's house
policy: ``account_id`` is nullable there (NULL *is* the house row), and Postgres treats NULLs as
distinct inside a unique constraint — so ``(org, NULL)`` could be inserted any number of times and
"the house policy" would quietly become "whichever house policy the query happened to return
first". The partial unique index is what makes a second one impossible rather than unlikely; the
same lesson ``dim_key`` learned by being ``NOT NULL DEFAULT ''``, applied where that shape is not
available.

``google_ads_decisions`` has **no** unique constraint, and that is a decision rather than an
omission. It is an append-only history: the same term may be excluded in March, un-excluded in
June and excluded again in September, and all three are true. Idempotency is a pre-check in the
service — the inversion of the payments rule (CLAUDE.md §10), which exists because a duplicate
``InvoicePayment`` is money counted twice, where a duplicate history row is a duplicate history
row. A unique index here would buy nothing and cost a 500 on an agent's ordinary second call.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = "f1c48d20a973"
down_revision: str | None = "d4f1a86e29c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("google_ads_decisions", "google_ads_policies")


def upgrade() -> None:
    op.create_table(
        "google_ads_policies",
        # Nullable: NULL is the agency's own house policy, which is what makes "resolve the
        # effective policy" one function over one record type instead of two shapes that drift.
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column(
            "protected_terms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "banned_phrases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "always_exclude",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("max_daily_budget", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("max_budget_increase_pct", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("max_cpc", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("waste_min_cost", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("waste_min_clicks", sa.Integer(), nullable=True),
        sa.Column("steering", sa.Text(), server_default="", nullable=False),
        sa.Column("ad_copy_rules", sa.Text(), server_default="", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["google_ads_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "account_id", name="uq_google_ads_policies_account"),
    )
    op.create_index(op.f("ix_google_ads_policies_org_id"), "google_ads_policies", ["org_id"])
    op.create_index(
        "uq_google_ads_policies_house",
        "google_ads_policies",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("account_id IS NULL"),
    )

    op.create_table(
        "google_ads_decisions",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("subject_type", sa.String(length=24), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("subject_key", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=64), server_default="account", nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("applied", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="manual", nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        # SET NULL rather than CASCADE: a colleague leaving must not delete the record of what
        # they decided about a client's advertising. The snapshotted name is what survives (§16).
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("decided_by_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("impersonator_name", sa.String(length=255), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by_name", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["google_ads_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_google_ads_decisions_org_id"), "google_ads_decisions", ["org_id"])
    # The lookup the whole table exists for: "has anything already been decided about this term?"
    op.create_index(
        "ix_google_ads_decisions_subject",
        "google_ads_decisions",
        ["org_id", "account_id", "subject_type", "subject_key"],
    )
    op.create_index(
        "ix_google_ads_decisions_recent",
        "google_ads_decisions",
        ["org_id", "account_id", "created_at"],
    )

    for table in _TABLES:
        enable_rls(table)


def downgrade() -> None:
    for table in _TABLES:
        disable_rls(table)
    op.drop_index("ix_google_ads_decisions_recent", table_name="google_ads_decisions")
    op.drop_index("ix_google_ads_decisions_subject", table_name="google_ads_decisions")
    op.drop_index(op.f("ix_google_ads_decisions_org_id"), table_name="google_ads_decisions")
    op.drop_table("google_ads_decisions")
    op.drop_index("uq_google_ads_policies_house", table_name="google_ads_policies")
    op.drop_index(op.f("ix_google_ads_policies_org_id"), table_name="google_ads_policies")
    op.drop_table("google_ads_policies")
