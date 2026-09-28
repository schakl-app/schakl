"""invoicing: the VAT return period

How often the agency files VAT — month, quarter or year — so the overview can sum the tax
charged over the span the return will actually cover. One additive column with a server
default, so this is a plain expand step (docs/WORKFLOW.md): every existing settings row reads
``quarter``, which is what most Dutch businesses file, and a release rolled back keeps the
column and simply stops reading it.

Nothing else reads the value. It changes no document, no total and no cron — an instance that
upgrades and sets nothing gains one figure on a screen.

Revision ID: ec56853d27b0
Revises: e4b8a1c6d3f7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "ec56853d27b0"
down_revision: str | None = "e4b8a1c6d3f7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "invoicing_settings",
        sa.Column(
            "vat_period",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'quarter'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("invoicing_settings", "vat_period")
