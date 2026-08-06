"""What a selection of invoices can have done to it in one go.

Delete, and deliberately nothing else. Everything else an invoice has is either money or its
place in a lifecycle: the amount, the client, the number, the status. A number is issued once
and a status moves by *doing* something — issuing, sending, recording a payment — each of which
is its own endpoint with its own rules. A control that set "status" across a selection would be
a way to skip all of them.

Delete is different, and it is the one a real inbox of drafts wants: a batch generated from the
wrong period, a run created twice by an automation. ``InvoiceService.delete`` allows **drafts
only** — an issued invoice is a numbered legal document, cancelled rather than removed — so a
mixed selection comes back as "3 verwijderd · 5 overgeslagen" with ``errors.invoicing.not_draft``
naming why, which is exactly the answer somebody clearing out drafts wants to see.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor
from app.core.tenancy import RequestContext
from app.modules.invoicing.models import Invoice
from app.modules.invoicing.service import InvoiceService


async def _delete(ctx: RequestContext, invoice: Any) -> None:
    """Through the service: it releases the time entries and subscription periods the draft had
    claimed, and reverts the quote it came from. A repository delete would strand all three."""
    await InvoiceService(ctx).delete(invoice.id)


INVOICE_BULK = BulkDescriptor(
    model=Invoice,
    entity="invoice",
    delete_permission="invoicing.invoice.delete",
    delete_row=_delete,
)
