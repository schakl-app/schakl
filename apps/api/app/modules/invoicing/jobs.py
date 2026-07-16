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
from app.core.email.senders import send_email
from app.core.events import SystemContext
from app.core.jobs import run_per_org
from app.core.models import Org
from app.core.timezone import org_zoneinfo
from app.modules.invoicing.emails import compose_reminder_email, load_transport
from app.modules.invoicing.models import (
    Invoice,
    InvoiceStatus,
    InvoicingSettings,
    Quote,
    QuoteStatus,
)

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


async def _remind_org(org: Org, session: AsyncSession) -> None:
    ctx = SystemContext(org=org, session=session)
    today = datetime.now(await org_zoneinfo(session, org.id)).date()

    await _expire_quotes(ctx, today)

    settings_row = await session.scalar(
        select(InvoicingSettings).where(InvoicingSettings.org_id == org.id)
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
                )
            )
        )
        .scalars()
        .all()
    )
    if not due:
        return

    transport = await load_transport(session, org.id)
    brand = org.name
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
        message = compose_reminder_email(invoice, brand, days_past)
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
