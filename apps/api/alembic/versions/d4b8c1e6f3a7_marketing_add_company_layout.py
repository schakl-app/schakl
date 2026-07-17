"""marketing_add_company_layout

Per-client marketing tab layout (issue #192): a ``layout`` JSONB on
``marketing_company_settings`` — per source an ordered tile list (absence = hidden), label
overrides, enabled drill-downs and the default charted metric. NULL = no curation.

**Expand half** of the expand/contract replacing ``show_key_events`` (#134,
docs/WORKFLOW.md): rows with the boolean off are migrated into an equivalent layout (GA4
tiles minus keyEvents/conversions, drill-downs minus the by-event breakdown); the boolean
stays, is kept coherent by the service, and is still honoured wherever no layout exists — so
the previous image keeps working against this schema and rollback is safe. The **contract**
half (stop reading the boolean, drop the column and its endpoint field) ships next release.

The backfill is idempotent (only rows with ``layout IS NULL``) and org-scoped per row by
construction (a plain UPDATE over the tenant-scoped table under RLS as ``schakl_app``).

Revision ID: d4b8c1e6f3a7
Revises: c8f2a5d7e9b4
Create Date: 2026-07-16 12:00:00.000000
"""
from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4b8c1e6f3a7'
down_revision: str | None = 'c8f2a5d7e9b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the #134-era vocabulary — a migration never imports evolving app code
# (docs/WORKFLOW.md): if the metric list grows later, THIS migration must keep writing the
# layout that matched the boolean's semantics at the time it shipped.
_GA4_TILES_WITHOUT_KEY_EVENTS = [
    "sessions", "totalUsers", "newUsers", "engagementRate", "totalRevenue",
]
_GA4_DRILLDOWNS_WITHOUT_KEY_EVENTS = ["top_pages", "channels", "devices"]


def upgrade() -> None:
    op.add_column(
        'marketing_company_settings',
        sa.Column('layout', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Migrate the boolean's meaning into an explicit layout, once, idempotently.
    layout = json.dumps(
        {
            "sources": {
                "ga4": {
                    "tiles": _GA4_TILES_WITHOUT_KEY_EVENTS,
                    "drilldowns": _GA4_DRILLDOWNS_WITHOUT_KEY_EVENTS,
                }
            }
        }
    )
    op.execute(
        sa.text(
            "UPDATE marketing_company_settings SET layout = CAST(:layout AS jsonb) "
            "WHERE show_key_events = false AND layout IS NULL"
        ).bindparams(layout=layout)
    )


def downgrade() -> None:
    op.drop_column('marketing_company_settings', 'layout')
