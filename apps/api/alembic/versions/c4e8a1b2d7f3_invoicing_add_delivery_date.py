"""invoicing_add_delivery_date

Revision ID: c4e8a1b2d7f3
Revises: a1c6d3e70f42
Create Date: 2026-08-01 12:00:00.000000

One nullable column: ``invoices.delivery_date`` — the *leverdatum* a Dutch invoice states when
the goods or service were delivered on a different day than the invoice was dated.

Purely additive, so an existing install upgrades unattended and the rollback drops only what
this created (docs/WORKFLOW.md's expand/contract rule has nothing to contract here). No
backfill: every existing invoice was delivered when it was dated as far as anyone recorded,
and inventing a value would be asserting something nobody entered. NULL means "not stated",
and the block that prints it is off by default for exactly that reason.

Everything else the template work needed is JSONB the schemas already validate — the template
config's layout/background/custom source, the seller's BIC and website, and the addressee's
``attn``/``client_number`` — so no column changes for any of it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1b2d7f3"
down_revision: str | None = "a1c6d3e70f42"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("delivery_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "delivery_date")
