"""invoicing: line kind on document lines + billed subscription periods

Two additive changes, both safe for an unattended self-host upgrade (docs/WORKFLOW.md):

* ``line_kind`` on invoice/quote lines — every existing line becomes ``product``, which is
  exactly how it renders today (one flat table), so nothing already sent changes shape.
* ``invoice_subscription_periods`` — the claim a hand-built invoice lays on a subscription
  period so the cycle cron does not bill it again. Backfilled from the invoices the cron
  itself raised, so the new table is authoritative from the first request after the upgrade
  rather than only for periods billed from here on.

Revision ID: d41f7c2a8b16
Revises: c5a1e7d3b904
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.core.rls import disable_rls, enable_rls

revision = "d41f7c2a8b16"
down_revision = "c5a1e7d3b904"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("invoice_lines", "quote_lines"):
        op.add_column(
            table,
            sa.Column(
                "line_kind",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'product'"),
            ),
        )

    # Lines the subscription/domain cycle raised are, by construction, recurring lines: the
    # only invoices carrying a subscription_id or domain_id are the ones those crons drafted.
    op.execute(
        """
        UPDATE invoice_lines AS l
           SET line_kind = 'subscription'
          FROM invoices AS i
         WHERE i.id = l.invoice_id
           AND i.org_id = l.org_id
           AND (i.subscription_id IS NOT NULL OR i.domain_id IS NOT NULL)
        """
    )
    # Lines built from time entries: the invoice has time-entry links and the line is priced
    # per hour. `unit` is the only marker those builds left behind (service.from_time).
    op.execute(
        """
        UPDATE invoice_lines AS l
           SET line_kind = 'hours'
         WHERE l.unit IN ('uur', 'uren', 'hour', 'hours', 'h', 'u')
           AND EXISTS (
                 SELECT 1 FROM invoice_time_entries AS e
                  WHERE e.invoice_id = l.invoice_id AND e.org_id = l.org_id
               )
        """
    )

    op.create_table(
        "invoice_subscription_periods",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "org_id",
            "subscription_id",
            "period_end",
            name="uq_invoice_subscription_periods_period",
        ),
    )
    op.create_index(
        "ix_invoice_subscription_periods_invoice_id",
        "invoice_subscription_periods",
        ["invoice_id"],
    )
    op.create_index(
        "ix_invoice_subscription_periods_org_id", "invoice_subscription_periods", ["org_id"]
    )

    # RLS, like every domain table (Golden Rule 1).
    enable_rls("invoice_subscription_periods")

    # Backfill the periods the cycle cron already billed, so the new lookup knows about them.
    op.execute(
        """
        INSERT INTO invoice_subscription_periods
              (id, org_id, invoice_id, subscription_id, period_start, period_end)
        SELECT gen_random_uuid(), i.org_id, i.id, i.subscription_id, i.period_start,
               i.period_end
          FROM invoices AS i
         WHERE i.subscription_id IS NOT NULL
           AND i.period_end IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_invoice_subscription_periods_period DO NOTHING
        """
    )


def downgrade() -> None:
    disable_rls("invoice_subscription_periods")
    op.drop_table("invoice_subscription_periods")
    for table in ("invoice_lines", "quote_lines"):
        op.drop_column(table, "line_kind")
