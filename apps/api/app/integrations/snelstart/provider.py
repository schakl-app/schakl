"""The ``AccountingProvider`` adapter (#31's seam, shipped with #207). Business-licensed.

``invoicing`` shipped this seam before any provider existed, along with the three routes that
use it: ``GET /invoicing/providers`` (the Boekhouding settings list), ``POST
/invoicing/invoices/{id}/export?provider=…`` and ``GET /invoicing/invoices/{id}/refs``. Filling
it in costs one file and lights all three up with **no edit to invoicing at all**, which is the
whole point of the seam and worth honouring rather than routing around.

So there are two ways to push one invoice, and they are not a duplication:

* ``POST /snelstart/accounts/{id}/push/invoices`` is this module's own — it takes a selection,
  runs each row in its own savepoint, attaches PDFs and writes a sync run somebody can read.
* ``POST /invoicing/invoices/{id}/export?provider=snelstart`` is the generic one, reached from
  the invoice's own screen by anyone who does not know or care which package is connected.

Both end in :meth:`SnelstartSyncService.push_invoice`, so they cannot disagree about
idempotency — which is the property that actually matters.
"""

from __future__ import annotations

from typing import Any

from app.core.tenancy import RequestContext
from app.errors import AppError
from app.integrations.snelstart.models import SnelstartAccount, SnelstartAccountStatus
from app.modules.invoicing.accounting import ExportResult


class SnelstartAccountingProvider:
    """SnelStart, as the provider-independent seam sees it."""

    key = "snelstart"
    label = "SnelStart"

    async def export_invoice(
        self,
        ctx: RequestContext,
        invoice: Any,
        seller: dict[str, Any],
    ) -> ExportResult:
        """Push one invoice, choosing the administration when there is only one to choose.

        ``seller`` is ignored on purpose. It is schakl's own identity, and SnelStart already
        knows whose administration this is — the koppelsleutel *is* that answer. Writing our
        copy of the seller block into somebody's books would be schakl asserting something the
        ledger is authoritative about.

        **Never guesses which account.** An agency holding two connected administrations gets a
        refusal naming the ambiguity rather than an invoice booked into whichever row was
        created first — ``mollie``'s rule, and the consequence of breaking it here is an
        invoice in the wrong company's ledger.
        """
        from app.integrations.snelstart.sync import SnelstartSyncService

        service = SnelstartSyncService(ctx)
        rows = list(
            (
                await ctx.session.execute(
                    ctx.repo(SnelstartAccount)
                    .scoped_select()
                    .where(
                        SnelstartAccount.active.is_(True),
                        SnelstartAccount.status == SnelstartAccountStatus.ACTIVE.value,
                        SnelstartAccount.client_key_encrypted.is_not(None),
                    )
                    .order_by(SnelstartAccount.name)
                )
            ).scalars()
        )
        if not rows:
            raise AppError(
                "snelstart_not_connected", "errors.snelstart.not_connected", status_code=409
            )
        if len(rows) > 1:
            raise AppError(
                "snelstart_account_ambiguous",
                "errors.snelstart.account_ambiguous",
                status_code=409,
            )

        result = await service.push_invoice(rows[0], invoice)
        if not result.ok or not result.external_id:
            raise AppError(
                "snelstart_push_failed",
                result.error_key or "errors.snelstart.request_failed",
                status_code=502,
            )
        return ExportResult(
            external_id=result.external_id,
            payload={
                "factuurnummer": result.external_code,
                "action": result.action,
                "administration": rows[0].administration_name,
                # Reported rather than swallowed: a rate the administration's own table could
                # not confirm was taxed by a fallback, and that belongs in the record of the
                # export rather than only in a log line.
                "guessed_rates": result.guessed_rates,
            },
        )
