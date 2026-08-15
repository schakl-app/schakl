"""reporting: per-client section choice, and how positions are reported

Three additive columns, all nullable-or-defaulted, so an instance that upgrades unattended keeps
behaving exactly as it did (docs/WORKFLOW.md's rule for breaking changes — this one is not).

- ``report_profiles.sections`` — one client's own section on/off diff over the template's
  layout (#373). ``{}`` means "inherit everything", which is what every existing profile means
  today, so the default is the whole migration's compatibility story.
- ``marketing_settings.rankings`` — the agency's house keyword-positions settings. NULL = the
  code defaults, which are the behaviour an agency that never opens the screen should get.
- ``marketing_company_settings.rankings`` — the same, per client. NULL = inherit.

Revision ID: f4c81a92d7be
Revises: a4d7e2c91b58
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f4c81a92d7be"
down_revision = "a4d7e2c91b58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_profiles",
        sa.Column(
            "sections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "marketing_settings",
        sa.Column("rankings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "marketing_company_settings",
        sa.Column("rankings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("marketing_company_settings", "rankings")
    op.drop_column("marketing_settings", "rankings")
    op.drop_column("report_profiles", "sections")
