"""marketing_add_portal_source_labels

Revision ID: 7502c9155ca8
Revises: e7a94c1d5b26
Create Date: 2026-08-31 14:30:00.000000

One nullable JSONB column on ``marketing_settings`` (#446): what a client is told each
marketing source is called. Purely additive — NULL means the code default, which is what every
existing install shows today for the Google sources and a vendor-free name for the keyed ones —
so the previous image runs unchanged against this schema and ``downgrade`` only drops it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7502c9155ca8"
down_revision: str | None = "e7a94c1d5b26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketing_settings",
        sa.Column(
            "portal_source_labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("marketing_settings", "portal_source_labels")
