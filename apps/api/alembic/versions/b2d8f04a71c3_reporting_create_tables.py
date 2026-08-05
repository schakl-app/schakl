"""reporting_create_tables

The periodic client report (issue #300): tones, templates, per-client profiles, org settings
and the runs themselves.

Every table is org-scoped with RLS forced, like every domain table (Golden Rule 1). ``reports``
deliberately carries **no FK on ``company_id``**: a report a client received is a historical
fact and must outlive the company row it described — the activity-trail precedent (§16), the
same reasoning ``ai_reports`` already uses.

Revision ID: b2d8f04a71c3
Revises: a1c7e3b09f42
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls

revision = "b2d8f04a71c3"
down_revision = "a1c7e3b09f42"
branch_labels = None
depends_on = None

_TABLES = ("report_tones", "report_templates", "report_profiles", "reporting_settings", "reports")


def _org_column() -> sa.Column:
    return sa.Column(
        "org_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("orgs.id", ondelete="CASCADE"),
        nullable=False,
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "report_tones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org_column(),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "banned_phrases", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "preferred_phrases", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint("org_id", "key", name="uq_report_tones_key"),
    )
    op.create_index("ix_report_tones_org_id", "report_tones", ["org_id"])
    op.create_index("ix_report_tones_org_active", "report_tones", ["org_id", "active"])

    op.create_table(
        "report_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org_column(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("audience", sa.String(16), nullable=False, server_default="client"),
        sa.Column("design", sa.String(32), nullable=False, server_default="standard"),
        sa.Column("layout", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("custom_html", sa.Text(), nullable=True),
        sa.Column("custom_css", sa.Text(), nullable=True),
        sa.Column("accent_color", sa.String(16), nullable=True),
        sa.Column(
            "cover_image_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("intro_text", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_timestamps(),
    )
    op.create_index("ix_report_templates_org_id", "report_templates", ["org_id"])
    op.create_index(
        "ix_report_templates_org_audience", "report_templates", ["org_id", "audience"]
    )

    op.create_table(
        "report_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org_column(),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_tones.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "internal_template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("locale", sa.String(8), nullable=False, server_default="nl"),
        *[
            sa.Column(name, sa.Text(), nullable=True)
            for name in (
                "business_context", "goals", "seo_focus", "sea_focus", "key_services",
                "priority_pages", "conversion_goals", "scope_notes", "avoid_topics",
            )
        ],
        sa.Column("recipients", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("schedule", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "internal_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_timestamps(),
        sa.UniqueConstraint("org_id", "company_id", name="uq_report_profiles_company"),
    )
    op.create_index("ix_report_profiles_org_id", "report_profiles", ["org_id"])
    op.create_index("ix_report_profiles_company_id", "report_profiles", ["company_id"])

    op.create_table(
        "reporting_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org_column(),
        sa.Column("schedule", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("default_locale", sa.String(8), nullable=False, server_default="nl"),
        sa.Column("footer_text", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("org_id", name="uq_reporting_settings_org"),
    )
    op.create_index("ix_reporting_settings_org_id", "reporting_settings", ["org_id"])

    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org_column(),
        # No FK: the report outlives the company it describes (§16's activity-trail rule).
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("audience", sa.String(16), nullable=False, server_default="client"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("locale", sa.String(8), nullable=False, server_default="nl"),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("compare_start", sa.Date(), nullable=True),
        sa.Column("compare_end", sa.Date(), nullable=True),
        sa.Column("data_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("narrative", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("edited_sections", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "pdf_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_to", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "generated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("generated_by_name", sa.String(255), nullable=True),
        *_timestamps(),
        # Idempotency: one report per client per audience per period. This constraint is what
        # makes a re-run update a row rather than mail a client a second copy.
        sa.UniqueConstraint(
            "org_id", "company_id", "audience", "period_start",
            name="uq_reports_company_period",
        ),
    )
    op.create_index("ix_reports_org_id", "reports", ["org_id"])
    op.create_index("ix_reports_company_id", "reports", ["company_id"])
    op.create_index("ix_reports_org_status", "reports", ["org_id", "status"])
    op.create_index(
        "ix_reports_org_company_period", "reports", ["org_id", "company_id", "period_start"]
    )

    for table in _TABLES:
        enable_rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        disable_rls(table)
        op.drop_table(table)
