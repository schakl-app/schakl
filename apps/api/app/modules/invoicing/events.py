"""``subscription.due`` / ``domain.due`` → draft invoice (issues #207, #250).

The subscriptions cycle cron and the domains renewal cron own their *agreements* and
deliberately raise no invoices; they emit ``*.due`` with everything a consumer needs
(amount, currency, period, lines). These handlers are that consumer: one **draft** per
(source record, period), idempotent both ways — a lookup first, and the partial unique
indexes on ``invoices`` as the backstop — so a re-run, a crash-resume or a double emit can
never double-bill a client (#31's hard rule).

They run on the emitter's context (the cron's ``SystemContext``): no permission check — an
event side effect rides the emitter's authority — and the actor on the trail is the system,
which is exactly who raised the document.

**How far the document goes is the tenant's call** (:class:`AutoInvoiceMode`), resolved per
agreement over an org default. ``off`` raises nothing and loses nothing — the period stays
unclaimed and the editor's picker offers it, which is the manual path. ``draft`` is the
default and what this consumer always did. ``issue`` and ``send`` go further, and are an
explicit owner decision overriding #31's original *"do not auto-finalise financial
documents"* (recorded in ``docs/INVOICING.md``): they are opt-in, per-agreement overridable,
and they degrade one step rather than propagate, because the two of them are the steps a
delete cannot undo.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select, text

from app.core.activity import ActivityService
from app.core.billing import resolve_auto_invoice_mode
from app.core.events import EmitContext
from app.core.models import OrgSettings
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.i18n import translate
from app.modules.invoicing.calc import LineInput, compute_totals, line_amount
from app.modules.invoicing.models import (
    AutoInvoiceMode,
    Invoice,
    InvoiceDomainPeriod,
    InvoiceLine,
    InvoiceStatus,
    InvoiceSubscriptionPeriod,
    InvoicingSettings,
    LineKind,
    TaxRate,
)
from app.modules.invoicing.service import tax_label

logger = logging.getLogger("schakl.invoicing")


def _decimal(value: Any, fallback: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(fallback)


def _parse_period(payload: dict[str, Any]) -> tuple[date | None, date] | None:
    try:
        period_end = date.fromisoformat(str(payload.get("period_end")))
        period_start = (
            date.fromisoformat(str(payload["period_start"]))
            if payload.get("period_start")
            else None
        )
    except ValueError:
        return None
    return period_start, period_end


def _period_label(period_start: date | None, period_end: date) -> str:
    if period_start:
        return f"{period_start.strftime('%d-%m-%Y')}-{period_end.strftime('%d-%m-%Y')}"
    return period_end.strftime("%d-%m-%Y")


def _resolve_mode(override: Any, settings_row: InvoicingSettings | None) -> AutoInvoiceMode:
    """This module's binding of the core rule (:func:`resolve_auto_invoice_mode`) to the org's
    settings row — kept as a named seam because every ``*.due`` handler reads it the same way,
    and because the backlog report resolves the identical question from the other side."""
    return resolve_auto_invoice_mode(
        override, settings_row.auto_invoice_mode if settings_row else None
    )


async def _auto_issue(
    ctx: EmitContext,
    invoice: Invoice,
    settings_row: InvoicingSettings | None,
    *,
    send: bool,
) -> None:
    """Issue the draft the cron just raised, and flag it for the send pass if asked.

    Issuing here is safe to do inline: it is a number allocation and a status flip in the
    transaction that created the invoice, so the two commit or fail together. **Sending is
    not**, and is deliberately deferred to ``jobs.py`` — ``run_per_org`` gives a whole org one
    transaction, so mailing here would let a later agreement's failure roll back an invoice
    whose e-mail had already reached the client. A flag written in this transaction is only
    ever read for an invoice that committed.

    A failure degrades one step instead of propagating: an org that cannot issue (no seller
    name) keeps its draft and is told once on the trail, rather than losing the month's
    billing to an exception the cron cannot answer.
    """
    from app.modules.invoicing.service import InvoicingSettingsService, _customer_snapshot

    activity = ActivityService(ctx)
    try:
        if not (settings_row and (settings_row.company_details or {}).get("name")):
            raise AppError(
                "validation", "errors.invoicing.seller_incomplete", status_code=400
            )
        today = datetime.now(await org_zoneinfo(ctx.session, ctx.org.id)).date()
        due_days = settings_row.default_due_days if settings_row else 14
        number = await InvoicingSettingsService(ctx).allocate_number("invoice")
        company = (
            await ctx.session.execute(
                text(
                    "SELECT id, name, legal_name, invoice_email, vat_number, coc_number,"
                    " address_line1, house_number, address_line2, postal_code, city, country,"
                    " client_number FROM companies WHERE id = :cid AND org_id = :oid"
                ),
                {"cid": invoice.company_id, "oid": ctx.org.id},
            )
        ).mappings().first()
        await ctx.repo(Invoice).update(
            invoice,
            number=number,
            status=InvoiceStatus.OPEN.value,
            issue_date=today,
            due_date=today + timedelta(days=due_days),
            # Freeze the bill-to at the moment the document becomes real, exactly as the
            # manual issue does — a company that moves later never rewrites what was sent.
            customer=(
                _customer_snapshot(company, email=(invoice.customer or {}).get("email"))
                if company is not None
                else invoice.customer
            ),
            auto_send_pending=send,
        )
        await activity.record("invoice", invoice.id, "issued", {"number": number, "auto": True})
    except AppError as exc:
        # Recorded, not raised: the draft is worth more than the automation, and a cron that
        # threw here would take the rest of the org's billing down with it.
        await activity.record(
            "invoice", invoice.id, "auto_issue_failed", {"reason": exc.message_key}
        )
        logger.warning(
            "auto-issue failed for invoice %s in org %s: %s",
            invoice.id, ctx.org.slug, exc.message_key,
        )


async def _draft_period_invoice(
    ctx: EmitContext,
    *,
    company_id: Any,
    link_field: str,
    link_id: Any,
    period_start: date | None,
    period_end: date,
    raw_lines: list[dict[str, Any]],
    reference: str | None,
    currency: str,
    mode: AutoInvoiceMode,
    line_kind: LineKind,
) -> None:
    """The shared drafting core: company snapshot, org tax defaults, snapshotted lines,
    recomputed totals, one DRAFT invoice carrying ``link_field`` for idempotency — then as
    far towards the client as ``mode`` says (:class:`AutoInvoiceMode`)."""
    org_id = ctx.org.id
    company = (
        await ctx.session.execute(
            text("SELECT id, name, legal_name, invoice_email, vat_number, coc_number,"
                 " address_line1, house_number, address_line2, postal_code, city, country,"
                 " client_number FROM companies WHERE id = :cid AND org_id = :oid"),
            {"cid": company_id, "oid": org_id},
        )
    ).mappings().first()
    if company is None:  # the agreement outlived its client — nothing to bill
        logger.warning("%s due for unknown company %s in org %s", link_field, company_id, org_id)
        return

    settings_row = await ctx.session.scalar(
        select(InvoicingSettings).where(InvoicingSettings.org_id == org_id)
    )
    org_settings = await ctx.session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == org_id)
    )
    locale = org_settings.default_locale if org_settings else "nl"
    include_tax = settings_row.prices_include_tax if settings_row else False
    default_rate = None
    rate_id = settings_row.default_tax_rate_id if settings_row else None
    if rate_id is not None:
        default_rate = await ctx.session.scalar(
            select(TaxRate).where(TaxRate.org_id == org_id, TaxRate.id == rate_id)
        )
    if default_rate is None:
        default_rate = await ctx.session.scalar(
            select(TaxRate)
            .where(
                TaxRate.org_id == org_id,
                TaxRate.is_default.is_(True),
                TaxRate.active.is_(True),
            )
            .limit(1)
        )

    line_rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_lines):
        quantity = _decimal(raw.get("quantity"), "1")
        unit_price = _decimal(raw.get("unit_amount"))
        line_rows.append(
            {
                "position": index,
                # Whose cycle raised it: an agreement's period is a subscription line, a
                # registrar's year is a domain line. Both recur; they are not the same item
                # to the person reconciling the month (#302).
                "line_kind": line_kind.value,
                "description": (raw.get("description") or reference or "")[:512] or "—",
                "quantity": quantity,
                "unit": None,
                "unit_price": unit_price,
                "tax_rate_id": default_rate.id if default_rate else None,
                "tax_rate_pct": default_rate.rate if default_rate else Decimal(0),
                "tax_name": tax_label(default_rate.label_i18n, locale) if default_rate else "",
                "tax_category": default_rate.category if default_rate else "standard",
                "amount": line_amount(quantity, unit_price),
            }
        )
    totals = compute_totals(
        [
            LineInput(
                quantity=row["quantity"],
                unit_price=row["unit_price"],
                tax_rate_pct=row["tax_rate_pct"],
                tax_category=row["tax_category"],
                tax_name=row["tax_name"],
            )
            for row in line_rows
        ],
        prices_include_tax=include_tax,
    )

    from app.modules.invoicing.service import _customer_snapshot

    # The shared builder, not a second copy of it. This was a hand-written dict, and the two
    # had already drifted: it omitted ``client_number``, so a draft the subscription cron raised
    # printed a bill-to without the klantnummer the same client's hand-made invoice carries.
    # The client-label / legal-name split gave the copies a second thing to disagree about, and
    # the answer to "which name does an invoice say?" must not depend on who raised it.
    customer = _customer_snapshot(company, email=None)
    invoices = ctx.repo(Invoice)
    invoice = await invoices.create(
        company_id=uuid.UUID(str(company_id)),
        customer=customer,
        currency=(currency or "EUR").upper(),
        locale=locale,
        intro=None,
        notes=None,
        template_id=settings_row.default_template_id if settings_row else None,
        prices_include_tax=include_tax,
        period_start=period_start,
        period_end=period_end,
        reference=reference,
        subtotal=totals.subtotal,
        tax_total=totals.tax_total,
        total=totals.total,
        **{link_field: uuid.UUID(str(link_id))},
    )
    lines = ctx.repo(InvoiceLine)
    for row in line_rows:
        # Every line carries the period it bills, so an edit of this draft round-trips its
        # claim instead of silently handing the month back to the cron that raised it.
        await lines.create(
            invoice_id=invoice.id,
            **row,
            **{link_field: uuid.UUID(str(link_id))},
            period_start=period_start,
            period_end=period_end,
        )
    # The cron's own claim on the period, in the same table a hand-built invoice writes to —
    # so "has this period been billed?" has exactly one answer to look up, whichever raised it.
    claim_model = (
        InvoiceSubscriptionPeriod if link_field == "subscription_id" else InvoiceDomainPeriod
    )
    await ctx.repo(claim_model).create(
        invoice_id=invoice.id,
        **{link_field: uuid.UUID(str(link_id))},
        period_start=period_start,
        period_end=period_end,
    )
    await ActivityService(ctx).record_created(
        "invoice",
        invoice.id,
        {link_field: str(link_id), "period_end": str(period_end)},
    )
    logger.info(
        "drafted invoice for %s %s period %s in org %s",
        link_field, link_id, period_end, ctx.org.slug,
    )
    if mode.issues:
        await _auto_issue(ctx, invoice, settings_row, send=mode.sends)


async def on_subscription_due(ctx: EmitContext, payload: dict[str, Any]) -> None:
    subscription_id = payload.get("subscription_id")
    company_id = payload.get("company_id")
    period = _parse_period(payload)
    if period is None:
        logger.warning("subscription.due with unparsable period in org %s", ctx.org.slug)
        return
    period_start, period_end = period
    if not (subscription_id and company_id):
        return

    # Automation off: raise nothing and *say* nothing. The period is not lost — the cycle
    # advanced, nothing claimed it, and the editor's picker enumerates exactly the periods
    # no document holds. That is the manual path, and it is why turning automation off costs
    # a click rather than a month of billing.
    settings_row = await ctx.session.scalar(
        select(InvoicingSettings).where(InvoicingSettings.org_id == ctx.org.id)
    )
    mode = _resolve_mode(payload.get("auto_invoice_mode"), settings_row)
    if mode is AutoInvoiceMode.OFF:
        return

    # Idempotency, part one: the cheap lookup (the unique index is part two).
    #
    # The claim table is what makes "already paid" answerable for a period a **human**
    # billed: a hand-built invoice carrying this subscription's month alongside hours and
    # products has no ``invoices.subscription_id`` to find, but it does hold a claim. The
    # invoices lookup stays for rows drafted before the claim table existed and never
    # backfilled.
    claimed = await ctx.session.scalar(
        select(InvoiceSubscriptionPeriod.id).where(
            InvoiceSubscriptionPeriod.org_id == ctx.org.id,
            InvoiceSubscriptionPeriod.subscription_id == subscription_id,
            InvoiceSubscriptionPeriod.period_end == period_end,
        )
    )
    if claimed is not None:
        logger.info(
            "subscription %s period %s already billed; skipping draft in org %s",
            subscription_id, period_end, ctx.org.slug,
        )
        return
    existing = await ctx.session.scalar(
        select(Invoice.id).where(
            Invoice.org_id == ctx.org.id,
            Invoice.subscription_id == subscription_id,
            Invoice.period_end == period_end,
        )
    )
    if existing is not None:
        return

    raw_lines = payload.get("lines") or []
    if not raw_lines:
        raw_lines = [
            {
                "description": (
                    f"{payload.get('name', '')} ({_period_label(period_start, period_end)})"
                ).strip(),
                "quantity": "1",
                "unit_amount": payload.get("amount") or "0",
            }
        ]
    await _draft_period_invoice(
        ctx,
        company_id=company_id,
        link_field="subscription_id",
        link_id=subscription_id,
        period_start=period_start,
        period_end=period_end,
        raw_lines=raw_lines,
        reference=payload.get("name"),
        currency=payload.get("currency") or "EUR",
        mode=mode,
        line_kind=LineKind.SUBSCRIPTION,
    )


async def on_domain_due(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """One draft per (domain, period) — #250's renewal loop, the subscription shape."""
    domain_id = payload.get("domain_id")
    company_id = payload.get("company_id")
    period = _parse_period(payload)
    if period is None:
        logger.warning("domain.due with unparsable period in org %s", ctx.org.slug)
        return
    period_start, period_end = period
    if not (domain_id and company_id):
        return

    settings_row = await ctx.session.scalar(
        select(InvoicingSettings).where(InvoicingSettings.org_id == ctx.org.id)
    )
    mode = _resolve_mode(payload.get("auto_invoice_mode"), settings_row)
    if mode is AutoInvoiceMode.OFF:
        return

    # Idempotency, part one: the claim table — which is what a **hand-picked** renewal writes.
    # Until it existed, a renewal billed by hand on a mixed invoice had no
    # ``invoices.domain_id`` to find, so the cron billed the year a second time; the lookup on
    # ``invoices`` stays for rows drafted before the table and never backfilled.
    claimed = await ctx.session.scalar(
        select(InvoiceDomainPeriod.id).where(
            InvoiceDomainPeriod.org_id == ctx.org.id,
            InvoiceDomainPeriod.domain_id == domain_id,
            InvoiceDomainPeriod.period_end == period_end,
        )
    )
    if claimed is not None:
        logger.info(
            "domain %s period %s already billed; skipping draft in org %s",
            domain_id, period_end, ctx.org.slug,
        )
        return
    existing = await ctx.session.scalar(
        select(Invoice.id).where(
            Invoice.org_id == ctx.org.id,
            Invoice.domain_id == domain_id,
            Invoice.period_end == period_end,
        )
    )
    if existing is not None:
        return

    org_settings = await ctx.session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == ctx.org.id)
    )
    locale = org_settings.default_locale if org_settings else "nl"
    raw_lines = [
        {
            "description": translate(
                "domains.renewal_line",
                locale,
                name=payload.get("name", ""),
                period=_period_label(period_start, period_end),
            ),
            "quantity": "1",
            "unit_amount": payload.get("amount") or "0",
        }
    ]
    await _draft_period_invoice(
        ctx,
        company_id=company_id,
        link_field="domain_id",
        link_id=domain_id,
        period_start=period_start,
        period_end=period_end,
        raw_lines=raw_lines,
        reference=payload.get("name"),
        currency=payload.get("currency") or "EUR",
        mode=mode,
        line_kind=LineKind.DOMAIN,
    )
