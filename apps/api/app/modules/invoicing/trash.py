"""What a client's documents mean for deleting the client (docs/TRASH.md).

An **issued** invoice, credit note or quote carries a number, was sent, and sits in the
client's books and possibly in the ledger — it must outlive the client, so it blocks: a
client with one is archived, never deleted. Until this rule the company cascade deleted paid,
ledger-booked invoices that ``DELETE /invoices/{id}?force=true`` would have refused, and
stranded ``time_entries.invoiced_at`` on hours that could then never be billed again. A
**draft** is not a record yet; it hides with the client and goes when the client is purged.
"""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

INVOICING_TRASH_DEPENDENTS = (
    TrashDependent(
        key="invoicing.invoices_issued",
        label_key="trash.dependent.invoicing.invoices_issued",
        blocks=True,
        count=count_by_column("invoices", "company_id", where="AND number IS NOT NULL"),
    ),
    TrashDependent(
        key="invoicing.quotes_issued",
        label_key="trash.dependent.invoicing.quotes_issued",
        blocks=True,
        count=count_by_column("quotes", "company_id", where="AND number IS NOT NULL"),
    ),
    TrashDependent(
        key="invoicing.invoice_drafts",
        label_key="trash.dependent.invoicing.invoice_drafts",
        blocks=False,
        count=count_by_column("invoices", "company_id", where="AND number IS NULL"),
    ),
    TrashDependent(
        key="invoicing.quote_drafts",
        label_key="trash.dependent.invoicing.quote_drafts",
        blocks=False,
        count=count_by_column("quotes", "company_id", where="AND number IS NULL"),
    ),
)
