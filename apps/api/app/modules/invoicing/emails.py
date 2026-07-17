"""Document e-mails (issue #207): composing in the *document's* locale, sending through the
org's configured transport (#17).

Two delivery paths share the composition:

- **Request path** (`deliver`): the transport row is read *before* the network call and the
  send itself runs inside ``ctx.release_db()`` — an SMTP round-trip must never pin a pooled
  DB connection (docs/PERFORMANCE.md). Failures raise, so the route reports honestly.
- **Cron path** (`jobs.py`): the worker has its own pool, reads the transport once per org
  and calls the sender directly — no request, no release dance.

Subjects and bodies come from the shared i18n catalogs (``app.i18n.translate``) keyed by the
document's own ``locale`` — a Dutch agency invoicing a German client mails in the document's
language, not the org's.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.core.email.senders import OutgoingEmail, Sender, send_email
from app.core.email.service import get_row
from app.errors import AppError
from app.i18n import translate


def _fmt_date(value: Any) -> str:
    """European dd-mm-yyyy — the product's date language everywhere (docs/UX.md)."""
    return value.strftime("%d-%m-%Y") if value else ""


def _fmt_money(amount: Any, currency: str) -> str:
    return f"{currency} {amount}"


def compose_invoice_email(invoice: Any, brand: str, message: str | None) -> OutgoingEmail:
    params = {
        "number": invoice.number or "",
        "company": (invoice.customer or {}).get("name") or "",
        "total": _fmt_money(invoice.total, invoice.currency),
        "due_date": _fmt_date(invoice.due_date),
        "brand": brand,
    }
    subject = translate("invoicing.email.invoice_subject", invoice.locale, **params)
    body = translate("invoicing.email.invoice_body", invoice.locale, **params)
    if message:
        body = f"{message.strip()}\n\n{body}"
    return OutgoingEmail(to="", subject=subject, text=body)


def compose_quote_email(quote: Any, brand: str, message: str | None) -> OutgoingEmail:
    params = {
        "number": quote.number or "",
        "company": (quote.customer or {}).get("name") or "",
        "total": _fmt_money(quote.total, quote.currency),
        "valid_until": _fmt_date(quote.valid_until),
        "brand": brand,
    }
    subject = translate("invoicing.email.quote_subject", quote.locale, **params)
    body = translate("invoicing.email.quote_body", quote.locale, **params)
    if message:
        body = f"{message.strip()}\n\n{body}"
    return OutgoingEmail(to="", subject=subject, text=body)


def compose_reminder_email(invoice: Any, brand: str, days_overdue: int) -> OutgoingEmail:
    params = {
        "number": invoice.number or "",
        "company": (invoice.customer or {}).get("name") or "",
        "total": _fmt_money(invoice.total, invoice.currency),
        "outstanding": _fmt_money(invoice.total - invoice.paid_total, invoice.currency),
        "due_date": _fmt_date(invoice.due_date),
        "days": days_overdue,
        "brand": brand,
    }
    subject = translate("invoicing.email.reminder_subject", invoice.locale, **params)
    body = translate("invoicing.email.reminder_body", invoice.locale, **params)
    return OutgoingEmail(to="", subject=subject, text=body)


async def load_transport(
    session: AsyncSession, org_id: Any
) -> tuple[str, dict, Sender] | None:
    """The org's transport, decrypted — read it *before* any ``release_db`` block: inside
    one, an org-scoped SELECT would run without the RLS GUC and fail closed."""
    row = await get_row(session, org_id)
    if row is None:
        return None
    return (
        row.provider,
        json.loads(decrypt(row.config_enc)),
        Sender(from_email=row.from_email, from_name=row.from_name, reply_to=row.reply_to),
    )


async def deliver(ctx: Any, message: OutgoingEmail) -> None:
    """Request-path send: transport read first, network inside ``release_db``, honest
    failure. Callers write their bookkeeping (sent_at, counts) *after* this returns."""
    transport = await load_transport(ctx.session, ctx.org.id)
    if transport is None:
        raise AppError(
            "email_not_configured", "errors.email_not_configured", status_code=400
        )
    provider, config, sender = transport
    async with ctx.release_db():
        ok, error = await send_email(provider, config, sender, message)
    if not ok:
        raise AppError("email_failed", "errors.invoicing.email_failed", status_code=502)
