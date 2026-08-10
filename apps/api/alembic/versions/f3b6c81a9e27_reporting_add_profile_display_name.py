"""reporting_add_profile_display_name

What a client is called on their report, which is not always what an invoice calls them.
Purely additive and nullable, so the previous image runs unchanged against the new schema
(docs/WORKFLOW.md's expand/contract rule): ``NULL`` reads as *the company's own name*, which is
exactly what every existing report already prints.

Revision ID: f3b6c81a9e27
Revises: e5c2a9d41f80
Create Date: 2026-08-10

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3b6c81a9e27"
down_revision = "e5c2a9d41f80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_profiles",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("report_profiles", "display_name")
