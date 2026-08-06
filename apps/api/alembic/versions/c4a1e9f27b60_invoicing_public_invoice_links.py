"""invoicing: public invoice links

A capability token on the invoice, and the org-level switch that decides whether one is
ever minted. Both additive, so this is a plain expand step (docs/WORKFLOW.md): an instance
that rolls back keeps the column and simply stops reading it.

``public_token`` is deliberately **nullable with no backfill**. A token is minted when a
document is *issued*, so a draft never has one and the invoices already in the register get
theirs the first time somebody asks for a link (``ensure_public_token``) rather than in a
migration that would mint thousands of live bearer credentials for documents nobody is
going to send again.

The unique index is global rather than per org on purpose: the token is the *whole*
address, so two orgs holding the same string would make "which tenant is this" ambiguous at
exactly the lookup that has no session to fall back on. 256 bits of ``secrets`` makes a
collision impossible in practice; the index is what makes it impossible in fact.

Revision ID: c4a1e9f27b60
Revises: b3e1f7a24c05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4a1e9f27b60"
down_revision: str | None = "b3e1f7a24c05"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("public_token", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_invoices_public_token",
        "invoices",
        ["public_token"],
        unique=True,
        postgresql_where=sa.text("public_token IS NOT NULL"),
    )
    op.add_column(
        "invoicing_settings",
        sa.Column(
            "public_invoice_links",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # The landing page's poll clock (#304). NULL means "never polled", which is exactly what
    # every existing intent is, and exactly the value that lets the first poll through.
    op.add_column(
        "invoice_payment_intents",
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoice_payment_intents", "refreshed_at")
    op.drop_column("invoicing_settings", "public_invoice_links")
    op.drop_index("uq_invoices_public_token", table_name="invoices")
    op.drop_column("invoices", "public_token")
