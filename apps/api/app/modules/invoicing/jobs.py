"""Daily invoicing cron (issue #207): payment reminders + quote expiry, per org.

Reminders are **opt-in and bounded**: nothing sends until ``reminders_enabled``; the
schedule (``reminder_days`` past due) is tenant config; an invoice never gets more mails
than the schedule has steps; ``reminders_paused`` mutes one invoice. A failed send is
retried on the next run (the counter only advances on success) and recorded on the
invoice's activity trail the first day it was due to go out — visible, never silent
(#31's rule about finance failures).

"Days past due" is counted in the org's local calendar (§8) — a due date is a wall-clock
concept, and a reminder that fires a day early because of UTC is a wrong reminder.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.activity import ActivityService
from app.core.email.branding import apply_branding, load_brand
from app.core.email.senders import EmailAttachment, send_email
from app.core.events import SystemContext
from app.core.jobs import run_per_org
from app.core.models import Org
from app.core.timezone import org_zoneinfo
from app.modules.invoicing.emails import (
    compose_invoice_email,
    compose_reminder_email,
    load_transport,
)
from app.modules.invoicing.models import (
    Invoice,
    InvoiceKind,
    InvoiceStatus,
    InvoicingSettings,
    Quote,
    QuoteStatus,
)
from app.modules.invoicing.service import InvoiceService

logger = logging.getLogger("schakl.invoicing")


async def _expire_quotes(ctx: SystemContext, today) -> None:  # noqa: ANN001
    """Open quotes past their validity flip to ``expired`` — stored, so list filters and
    the pipeline agree with what the detail page already derived."""
    quotes = (
        (
            await ctx.session.execute(
                select(Quote).where(
                    Quote.org_id == ctx.org.id,
                    Quote.status == QuoteStatus.OPEN.value,
                    Quote.valid_until.is_not(None),
                    Quote.valid_until < today,
                )
            )
        )
        .scalars()
        .all()
    )
    for quote in quotes:
        quote.status = QuoteStatus.EXPIRED.value
        await ActivityService(ctx).record("quote", quote.id, "expired")
    if quotes:
        logger.info("expired %s quotes in org %s", len(quotes), ctx.org.slug)


async def _send_auto_issued(ctx: SystemContext, brand, transport) -> None:  # noqa: ANN001
    """Mail the invoices the billing cron issued under ``AutoInvoiceMode.SEND``.

    A separate pass, not part of the drafting handler, and the reason is transactional:
    ``run_per_org`` gives a whole org one transaction, so mailing at draft time would let a
    later agreement's failure roll back an invoice whose e-mail had already reached the
    client. ``auto_send_pending`` is written in the drafting transaction and read here, in
    the next job — so nothing is ever mailed for an invoice that did not commit.

    The failure discipline is the reminders one. A **transient** failure (the provider said
    no) leaves the flag up and retries tomorrow; a **structural** one (no recipient, no
    transport configured) clears it and records why, because retrying it daily would be
    noise and the invoice is issued and visible in the list either way.
    """
    pending = (
        (
            await ctx.session.execute(
                select(Invoice).where(
                    Invoice.org_id == ctx.org.id,
                    Invoice.auto_send_pending.is_(True),
                    Invoice.status == InvoiceStatus.OPEN.value,
                )
            )
        )
        .scalars()
        .all()
    )
    if not pending:
        return
    sent = 0
    for invoice in pending:
        to = (invoice.customer or {}).get("email")
        if not to:
            to = await ctx.session.scalar(
                text("SELECT invoice_email FROM companies WHERE id = :cid AND org_id = :oid"),
                {"cid": invoice.company_id, "oid": ctx.org.id},
            )
        if not to or transport is None:
            reason = "no_recipient" if not to else "email_not_configured"
            invoice.auto_send_pending = False  # structural: tomorrow would fail identically
            await ActivityService(ctx).record(
                "invoice", invoice.id, "auto_send_failed", {"reason": reason}
            )
            logger.warning(
                "auto-send for invoice %s in org %s failed: %s",
                invoice.number, ctx.org.slug, reason,
            )
            continue
        provider, config, sender = transport
        message = apply_branding(
            brand, compose_invoice_email(invoice, brand.brand_name, None)
        )
        message.to = to
        try:
            # The document itself, not just a summary — the manual send attaches it and an
            # automatic one has no excuse not to. Rendering is the same WeasyPrint pass the
            # preview and the download use, so the client receives the page they would see.
            service = InvoiceService(ctx)
            await service._attach([invoice], payments=True)  # noqa: SLF001 - same module
            content, filename = await service.document_pdf(invoice, "invoice")
            message.attachments.append(
                EmailAttachment(
                    filename=filename, content=content, mimetype="application/pdf"
                )
            )
        except Exception:  # noqa: BLE001 - a render fault must not cost the whole org's run
            # Deliberately still sent: an invoice mail naming the number and the amount is
            # worth more than silence, and the client can always be sent the PDF by hand.
            logger.exception(
                "auto-send could not render invoice %s in org %s; sending without attachment",
                invoice.number, ctx.org.slug,
            )
        ok, error = await send_email(provider, config, sender, message)
        if not ok:
            logger.warning(
                "auto-send for invoice %s in org %s failed: %s",
                invoice.number, ctx.org.slug, error,
            )
            continue  # flag untouched → retried tomorrow
        invoice.auto_send_pending = False
        invoice.sent_at = datetime.now(UTC)
        await ActivityService(ctx).record(
            "invoice", invoice.id, "sent", {"to": to, "auto": True}
        )
        sent += 1
    if sent:
        logger.info("auto-sent %s invoices in org %s", sent, ctx.org.slug)


async def _remind_org(org: Org, session: AsyncSession) -> None:
    ctx = SystemContext(org=org, session=session)
    today = datetime.now(await org_zoneinfo(session, org.id)).date()

    await _expire_quotes(ctx, today)

    settings_row = await session.scalar(
        select(InvoicingSettings).where(InvoicingSettings.org_id == org.id)
    )
    # The auto-send queue is independent of the reminder schedule: an org that never dunned
    # anyone can still have automation set to `send`, so this must not sit behind the
    # `reminders_enabled` gate below.
    await _send_auto_issued(
        ctx, await load_brand(session, org), await load_transport(session, org.id)
    )
    if (
        settings_row is None
        or not settings_row.reminders_enabled
        or not settings_row.reminder_days
    ):
        return
    schedule = sorted(settings_row.reminder_days)

    due = (
        (
            await session.execute(
                select(Invoice).where(
                    Invoice.org_id == org.id,
                    Invoice.status == InvoiceStatus.OPEN.value,
                    Invoice.reminders_paused.is_(False),
                    Invoice.due_date.is_not(None),
                    Invoice.due_date < today,
                    # Only chase what is actually still owed. `status = 'open'` alone dunned
                    # an invoice a credit note had written off, and — since a credit note is
                    # itself an open document that can never be paid — dunned the credit note
                    # too, asking the client to transfer a negative amount.
                    #
                    # The outstanding predicate below is the operative rule and covers every
                    # state crediting can reach. The kind filter is the narrower guarantee
                    # the renderer already makes ("a credit note never asks to be paid",
                    # test_invoicing_render.py): a credit note hand-written with positive
                    # lines is odd data, and dunning it would contradict the document it
                    # would arrive next to.
                    Invoice.kind != InvoiceKind.CREDIT_NOTE.value,
                    (
                        Invoice.total
                        - Invoice.paid_total
                        - Invoice.credited_total
                        + Invoice.applied_total
                    )
                    > 0,
                )
            )
        )
        .scalars()
        .all()
    )
    if not due:
        return

    transport = await load_transport(session, org.id)
    brand = await load_brand(session, org)
    for invoice in due:
        if invoice.reminder_count >= len(schedule):
            continue  # the schedule is exhausted — escalation is a human's call now
        threshold = schedule[invoice.reminder_count]
        days_past = (today - invoice.due_date).days
        if days_past < threshold:
            continue
        first_attempt_day = days_past == threshold
        to = (invoice.customer or {}).get("email")
        if not to:
            to = await session.scalar(
                text("SELECT invoice_email FROM companies WHERE id = :cid AND org_id = :oid"),
                {"cid": invoice.company_id, "oid": org.id},
            )
        if not to or transport is None:
            # Visible failure, once (the day it should have gone out), not daily noise.
            if first_attempt_day:
                reason = "no_recipient" if not to else "email_not_configured"
                await ActivityService(ctx).record(
                    "invoice", invoice.id, "reminder_failed", {"reason": reason}
                )
                logger.warning(
                    "reminder for invoice %s in org %s failed: %s",
                    invoice.number, org.slug, reason,
                )
            continue
        provider, config, sender = transport
        message = apply_branding(
            brand, compose_reminder_email(invoice, brand.brand_name, days_past)
        )
        message.to = to
        ok, error = await send_email(provider, config, sender, message)
        if not ok:
            if first_attempt_day:
                await ActivityService(ctx).record(
                    "invoice", invoice.id, "reminder_failed", {"reason": "provider"}
                )
            logger.warning(
                "reminder for invoice %s in org %s failed: %s", invoice.number, org.slug, error
            )
            continue  # counter untouched → retried tomorrow
        invoice.reminder_count += 1
        invoice.last_reminder_at = datetime.now(UTC)
        await ActivityService(ctx).record(
            "invoice", invoice.id, "reminder_sent",
            {"to": to, "level": invoice.reminder_count, "days_overdue": days_past},
        )
    logger.info("processed %s overdue invoices in org %s", len(due), org.slug)


async def invoicing_daily(ctx: dict) -> None:
    """ARQ entrypoint: reminders + quote expiry, per org via ``run_per_org``."""
    await run_per_org(_remind_org)
