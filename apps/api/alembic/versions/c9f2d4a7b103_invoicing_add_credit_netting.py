"""invoicing_add_credit_netting

A credit note used to be a document and nothing else: it corrected the paperwork and left
the money alone, so a fully credited invoice stayed open, stayed in arrears and kept being
dunned. Two counters make the correction reach the balance, mirroring ``paid_total``:

* ``credited_total`` — how much of this invoice issued credit notes have written off.
* ``applied_total``  — how much of *this* credit note was absorbed by the invoice it
  corrects. What is left over is a refund the client is owed.

Both are allocated once, when the credit note is issued, so the backfill has to walk the
existing credit notes in issue order rather than sum them.

Revision ID: c9f2d4a7b103
Revises: e2c5a90d47bf
"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision: str = "c9f2d4a7b103"
down_revision: str | None = "e2c5a90d47bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column(
            "credited_total",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "invoices",
        sa.Column(
            "applied_total",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
    )

    # Backfill: allocate every already-issued credit note against its source, oldest first,
    # exactly as `_apply_credit` will from now on. Drafts allocate nothing — they are not
    # documents yet — and a cancelled credit note has been withdrawn.
    #
    # Per org, binding `app.current_org` first: `invoices` is FORCE ROW LEVEL SECURITY and
    # migrations run as the unprivileged app role, so an unbound cross-org read matches **no
    # rows and raises nothing**. A backfill that silently does nothing is the worst outcome
    # available here — every existing credit note would stay unallocated while the migration
    # reported success. RLS also makes the same-tenant rule structural: a credit note can
    # only ever find a source inside its own org.
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        credits = bind.execute(
            sa.text(
                """
                SELECT id, credit_for_id, total
                FROM invoices
                WHERE kind = 'credit_note'
                  AND credit_for_id IS NOT NULL
                  AND status IN ('open', 'paid')
                ORDER BY issue_date NULLS LAST, created_at
                """
            )
        ).all()
        if credits:
            sources = {
                row.id: row
                for row in bind.execute(
                    sa.text(
                        """
                        SELECT id, total, paid_total, credited_total
                        FROM invoices WHERE id = ANY(:ids)
                        """
                    ),
                    {"ids": [c.credit_for_id for c in credits]},
                ).all()
            }
            credited: dict[object, Decimal] = {}
            for credit in credits:
                source = sources.get(credit.credit_for_id)
                if source is None:
                    continue
                already = credited.get(source.id, Decimal(source.credited_total or 0))
                room = max(
                    Decimal(0),
                    Decimal(source.total) - Decimal(source.paid_total) - already,
                )
                applied = min(-Decimal(credit.total), room)
                if applied <= 0:
                    continue
                credited[source.id] = already + applied
                bind.execute(
                    sa.text("UPDATE invoices SET applied_total = :v WHERE id = :id"),
                    {"v": applied, "id": credit.id},
                )
            for source_id, total in credited.items():
                bind.execute(
                    sa.text("UPDATE invoices SET credited_total = :v WHERE id = :id"),
                    {"v": total, "id": source_id},
                )

        # Settle the credit notes that already owe nothing. The old rule (`total > 0` before
        # comparing against `paid_total`) was unsatisfiable for a negative total, so a credit
        # note whose refund had genuinely been paid out still sat at `open` — counted as an
        # open document, reading as money in flight forever. Nothing recomputes an existing
        # row's status on its own, so the repair belongs here.
        #
        # `paid_at` takes the last payment's date rather than now(): the year it lands in is
        # what the paid-this-year tile reports, and a refund paid in December is not this
        # year's.
        bind.execute(
            sa.text(
                """
                UPDATE invoices AS i
                   SET status = 'paid',
                       paid_at = COALESCE(
                           (SELECT MAX(p.paid_on)::timestamptz
                              FROM invoice_payments p
                             WHERE p.invoice_id = i.id),
                           now()
                       )
                 WHERE i.kind = 'credit_note'
                   AND i.status = 'open'
                   AND i.total + i.applied_total - i.paid_total >= 0
                """
            )
        )


def downgrade() -> None:
    """Drops the columns; deliberately does **not** re-open the credit notes it settled.

    That repair fixed rows whose refund had genuinely been paid out and which only read as
    ``open`` because the old comparison could not be satisfied by a negative total. Undoing
    it would restore a wrong state, and once ``applied_total`` is gone there is nothing left
    to identify which rows were touched anyway.
    """
    op.drop_column("invoices", "applied_total")
    op.drop_column("invoices", "credited_total")
