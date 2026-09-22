"""marketing: SE Ranking's AI Search overview — settings, a Data API key, and the stored months

Purely additive, so an instance that upgrades unattended behaves exactly as it did
(docs/WORKFLOW.md): the overview is **off** until an agency switches it on, because every read
spends 800 of the agency's own SE Ranking units and an upgrade must not start spending them.

- ``marketing_settings.seranking_data_api_key_encrypted`` — an optional second key. SE Ranking
  issues a token per API (project and Data); NULL means *use the key already stored*, which is
  what every existing install has.
- ``marketing_settings.ai_search`` / ``marketing_company_settings.ai_search`` — the house
  defaults and one client's diff over them (``marketing.aisearch.AiSearchSettings``). NULL =
  inherit, at both levels.
- ``marketing_ai_search_snapshots`` — one row per client, per request, per month. A stored
  answer rather than a cache: a report printed from it must reprint the same figures later, and
  the call behind it is the most expensive one the module makes.

Revision ID: c8a3f5d1e7b2
Revises: b7e3c9d14f6a
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

revision = "c8a3f5d1e7b2"
down_revision = "b7e3c9d14f6a"
branch_labels = None
depends_on = None

_TABLE = "marketing_ai_search_snapshots"


def upgrade() -> None:
    op.add_column(
        "marketing_settings",
        sa.Column("seranking_data_api_key_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "marketing_settings",
        sa.Column("ai_search", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "marketing_company_settings",
        sa.Column("ai_search", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("target", sa.String(length=512), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("engine", sa.String(length=16), nullable=False),
        # '' rather than NULL for "let SE Ranking decide": NULLs are distinct inside a unique
        # constraint, so a nullable column here would permit the duplicates it exists to refuse.
        sa.Column("brand", sa.String(length=255), server_default=sa.text("''"), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column("data_month", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "time_series",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "discovered_brands",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_marketing_ai_search_snapshots_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
            name=op.f("fk_marketing_ai_search_snapshots_org_id_orgs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_ai_search_snapshots")),
        sa.UniqueConstraint(
            "org_id",
            "company_id",
            "target",
            "source",
            "scope",
            "engine",
            "brand",
            "period_month",
            name="uq_marketing_ai_search_snapshot_key",
        ),
    )
    op.create_index(op.f("ix_marketing_ai_search_snapshots_org_id"), _TABLE, ["org_id"])
    op.create_index(op.f("ix_marketing_ai_search_snapshots_company_id"), _TABLE, ["company_id"])
    op.create_index(
        "ix_marketing_ai_search_company_month", _TABLE, ["org_id", "company_id", "period_month"]
    )
    enable_rls(_TABLE)


def downgrade() -> None:
    disable_rls(_TABLE)
    op.drop_index("ix_marketing_ai_search_company_month", table_name=_TABLE)
    op.drop_index(op.f("ix_marketing_ai_search_snapshots_company_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_marketing_ai_search_snapshots_org_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
    op.drop_column("marketing_company_settings", "ai_search")
    op.drop_column("marketing_settings", "ai_search")
    op.drop_column("marketing_settings", "seranking_data_api_key_encrypted")
