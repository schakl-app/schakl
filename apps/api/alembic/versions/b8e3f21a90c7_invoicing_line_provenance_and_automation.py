"""invoicing: what a line bills, a domain-period claim, and the automation level

Four additive changes, all safe for an unattended self-host upgrade (docs/WORKFLOW.md) —
nothing is dropped, every new column is nullable or carries a server default, and the
defaults reproduce today's behaviour exactly.

* **Provenance on ``invoice_lines``** (``time_entry_ids``, ``subscription_id``,
  ``domain_id``, ``period_start``, ``period_end``). The claim tables alone could not say
  *which line* billed a thing, and the editor replaces lines wholesale on every save — so
  re-saving a draft posted lines that had forgotten their claims and the service released
  them, after which the cron billed the period a second time. The **backfill below is what
  makes that fix reach documents that already exist**, not only ones written from here on.
* **``invoice_domain_periods``** — the renewal half of ``invoice_subscription_periods``.
  Until now only ``invoices.domain_id`` answered "was this year billed?", which a hand-built
  invoice carrying eleven renewals could not populate; backfilled from the invoices the
  renewal cron itself raised, so it is authoritative from the first request after upgrade.
* **``invoicing_settings.auto_invoice_mode``** — the org's default automation level, seeded
  ``draft``, which is what every instance did before the column existed.
* **``subscriptions.auto_invoice_mode`` / ``domains.auto_invoice_mode``** — the per-agreement
  override, ``NULL`` = inherit. Nullable on purpose: NULL is a third state, not "off".

Revision ID: b8e3f21a90c7
Revises: d1a7f3b60c92
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.core.rls import disable_rls, enable_rls

revision = "b8e3f21a90c7"
down_revision = "d1a7f3b60c92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- what a line bills ------------------------------------------------------ #
    op.add_column(
        "invoice_lines",
        sa.Column(
            "time_entry_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "invoice_lines", sa.Column("subscription_id", PGUUID(as_uuid=True), nullable=True)
    )
    op.add_column("invoice_lines", sa.Column("domain_id", PGUUID(as_uuid=True), nullable=True))
    op.add_column("invoice_lines", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("invoice_lines", sa.Column("period_end", sa.Date(), nullable=True))

    # --- the renewal claim ------------------------------------------------------- #
    op.create_table(
        "invoice_domain_periods",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("domain_id", PGUUID(as_uuid=True), nullable=False),
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
            "org_id", "domain_id", "period_end", name="uq_invoice_domain_periods_period"
        ),
    )
    op.create_index(
        "ix_invoice_domain_periods_invoice_id", "invoice_domain_periods", ["invoice_id"]
    )
    op.create_index("ix_invoice_domain_periods_org_id", "invoice_domain_periods", ["org_id"])
    enable_rls("invoice_domain_periods")  # like every domain table (Golden Rule 1)

    op.execute(
        """
        INSERT INTO invoice_domain_periods
              (id, org_id, invoice_id, domain_id, period_start, period_end)
        SELECT gen_random_uuid(), i.org_id, i.id, i.domain_id, i.period_start, i.period_end
          FROM invoices AS i
         WHERE i.domain_id IS NOT NULL
           AND i.period_end IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_invoice_domain_periods_period DO NOTHING
        """
    )

    # --- attribute the existing claims to the lines that carry them --------------- #
    # This is what makes the round-trip fix reach documents that already exist: without it a
    # draft written before this release would post lines with no claim on its next save, and
    # the reconcile would hand the period straight back to the cron.
    #
    # The unambiguous case first: an invoice holding exactly one claim gives it to every
    # subscription-kind line it has. That covers every invoice a cron raised, which is the
    # overwhelming majority and the one the bug actually bit.
    for table, column in (
        ("invoice_subscription_periods", "subscription_id"),
        ("invoice_domain_periods", "domain_id"),
    ):
        op.execute(
            f"""
            UPDATE invoice_lines AS l
               SET {column} = c.{column},
                   period_start = c.period_start,
                   period_end   = c.period_end
              FROM {table} AS c
             WHERE c.invoice_id = l.invoice_id
               AND c.org_id = l.org_id
               AND l.line_kind = 'subscription'
               AND (
                     SELECT COUNT(*) FROM {table} AS c2
                      WHERE c2.invoice_id = l.invoice_id AND c2.org_id = l.org_id
                   ) = 1
            """  # noqa: S608 - table/column names are literals from the loop above
        )
        # The ambiguous case: several claims on one invoice (a hand-built document billing
        # three months at once). Both the picker and the cron bake the period into the line
        # description as dd-mm-yyyy, so match on that rather than guess — an unmatched line
        # stays unattributed, and the service's legacy guard then keeps its claims safe.
        op.execute(
            f"""
            UPDATE invoice_lines AS l
               SET {column} = c.{column},
                   period_start = c.period_start,
                   period_end   = c.period_end
              FROM {table} AS c
             WHERE c.invoice_id = l.invoice_id
               AND c.org_id = l.org_id
               AND l.line_kind = 'subscription'
               AND l.{column} IS NULL
               AND position(to_char(c.period_end, 'DD-MM-YYYY') in l.description) > 0
            """  # noqa: S608 - table/column names are literals from the loop above
        )

    # Hours are deliberately **not** backfilled: `invoice_time_entries` is invoice-level and a
    # grouped line covers many entries, with nothing recorded about which covered which.
    # `_reconcile_time_entries` reads an hours line carrying no ids as a legacy document and
    # leaves its links alone, so nothing is released that the document still bills.

    # --- the automation level ---------------------------------------------------- #
    op.add_column(
        "invoicing_settings",
        sa.Column(
            "auto_invoice_mode",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
    )
    op.add_column(
        "invoices",
        sa.Column(
            "auto_send_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # NULL = inherit the org default. Deliberately not NOT NULL DEFAULT 'draft': that would
    # freeze every existing agreement at today's level, and a later change to the org default
    # would then reach none of them.
    op.add_column(
        "subscriptions", sa.Column("auto_invoice_mode", sa.String(length=10), nullable=True)
    )
    op.add_column(
        "domains", sa.Column("auto_invoice_mode", sa.String(length=10), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("domains", "auto_invoice_mode")
    op.drop_column("subscriptions", "auto_invoice_mode")
    op.drop_column("invoices", "auto_send_pending")
    op.drop_column("invoicing_settings", "auto_invoice_mode")
    disable_rls("invoice_domain_periods")
    op.drop_table("invoice_domain_periods")
    for column in ("period_end", "period_start", "domain_id", "subscription_id",
                   "time_entry_ids"):
        op.drop_column("invoice_lines", column)
