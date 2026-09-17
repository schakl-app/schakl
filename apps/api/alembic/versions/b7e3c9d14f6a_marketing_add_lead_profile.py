"""marketing: the per-client measurement profile, and the house channel grouping

Two additive, nullable JSONB columns, so an instance that upgrades unattended keeps behaving
exactly as it did (docs/WORKFLOW.md): a client with no profile has no leads dashboard, which is
what every client meant before this release.

- ``marketing_company_settings.lead_profile`` — the *meetprofiel*: which GA4 events play which
  functional role (aanvraag, gestart, verzonden, fout, telefoon, e-mail, sollicitatie), which
  custom dimensions the dashboard may group by and what their values are called, the Google Ads
  conversion-action → service mapping, the measurement breakpoints, and which widgets this
  client sees. Shape validated in ``modules/marketing/leads/profile.py``.
- ``marketing_settings.channel_groups`` — the agency's house regrouping of GA4's
  ``sessionDefaultChannelGroup`` into Organisch / Advertenties / AI / Overig. NULL = the code
  default, which files Cross-network (Performance Max) under Advertenties.

Revision ID: b7e3c9d14f6a
Revises: d5e1f7a2c9b4
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7e3c9d14f6a"
down_revision = "d5e1f7a2c9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "marketing_company_settings",
        sa.Column("lead_profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "marketing_settings",
        sa.Column("channel_groups", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("marketing_settings", "channel_groups")
    op.drop_column("marketing_company_settings", "lead_profile")
