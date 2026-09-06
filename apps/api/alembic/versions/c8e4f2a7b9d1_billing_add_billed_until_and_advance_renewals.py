"""billing_add_billed_until_and_advance_renewals

Revision ID: c8e4f2a7b9d1
Revises: b7d3f9a2c4e6
Create Date: 2026-09-06 12:00:00.000000

Two things, one release, both about which periods a recurring charge has already covered.

**``billed_until``** on ``domains`` and ``subscriptions``: the operator's statement that
everything up to that date was invoiced already — by the system the record was migrated from,
on paper, by a predecessor. Additive, nullable, no default: an instance that types nothing bills
exactly as it did.

**A domain renewal is billed in advance.** Every renewal period was written as the year *behind*
its invoice date (``[date − 12m, date]``), so a renewal due 01-10-2026 read "01-10-2025 –
01-10-2026" on the backlog, the picker and the drafted line, when the invoice raised on that date
pays the register for the year *ahead*. The direction is now stated once in
``app.core.billing.period_span`` and the renewal callers all read it, which leaves the rows
already written on the old shape: a claim in ``invoice_domain_periods`` and the provenance on an
``invoice_lines`` row both say "boundary B is billed" as ``period_end = B``, and under the new
reading ``period_end = B`` means the boundary a year *earlier*. Left alone, every renewal ever
invoiced would be offered again — the duplicate the claim tables exist to prevent, re-entered
through the upgrade. So both are shifted one year forward (``start := end``, ``end := end + 1
year``), which is what those rows meant all along.

``invoices.period_start/period_end`` (the header line a cron-raised renewal invoice prints) is
**not** touched: an issued document is what the client received, and its line text carries the
old dates too. The header is not a claim key — ``on_domain_due`` finds the claim table first —
so leaving it costs nothing but the label on a document that has already been read.

Downgrade shifts the claims and line provenance back and drops the columns. ``+ interval '1
year'`` clamps 29 February to 28 February exactly as ``add_months`` does.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8e4f2a7b9d1'
down_revision: str | None = 'b7d3f9a2c4e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('domains', sa.Column('billed_until', sa.Date(), nullable=True))
    op.add_column('subscriptions', sa.Column('billed_until', sa.Date(), nullable=True))
    # The claim rows: the key a renewal is looked up by, so the shift is what keeps a renewal
    # invoiced last year from being offered again this year.
    op.execute(
        "UPDATE invoice_domain_periods "
        "SET period_start = period_end, period_end = (period_end + interval '1 year')::date"
    )
    # The lines' provenance, which the claim is rebuilt from on the next edit of the document.
    op.execute(
        "UPDATE invoice_lines "
        "SET period_start = period_end, period_end = (period_end + interval '1 year')::date "
        "WHERE domain_id IS NOT NULL AND period_end IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE invoice_lines "
        "SET period_end = period_start, period_start = (period_start - interval '1 year')::date "
        "WHERE domain_id IS NOT NULL AND period_start IS NOT NULL"
    )
    op.execute(
        "UPDATE invoice_domain_periods "
        "SET period_end = period_start, period_start = (period_start - interval '1 year')::date "
        "WHERE period_start IS NOT NULL"
    )
    op.drop_column('subscriptions', 'billed_until')
    op.drop_column('domains', 'billed_until')
