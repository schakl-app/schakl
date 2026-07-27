"""``subscription.due`` / ``domain.due`` → draft invoice (issues #207, #250).

The subscriptions cycle cron and the domains renewal cron own their *agreements* and
deliberately raise no invoices; they emit ``*.due`` with everything a consumer needs
(amount, currency, period, lines). These handlers are that consumer: one **draft** per
(source record, period), idempotent both ways — a lookup first, and the partial unique
indexes on ``invoices`` as the backstop — so a re-run, a crash-resume or a double emit can
never double-bill a client (#31's hard rule).

They run on the emitter's context (the cron's ``SystemContext``): no permission check — an
event side effect rides the emitter's authority — and the actor on the trail is the system,
which is exactly who raised the document. **Draft**, never issued: a human sends invoices
(#31: "do not auto-finalise financial documents").
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select, text

from app.core.activity import ActivityService
from app.core.events import EmitContext
from app.core.models import OrgSettings
from app.i18n import translate
from app.modules.invoicing.calc import LineInput, compute_totals, line_amount
from app.modules.invoicing.models import (
    Invoice,
    InvoiceLine,
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
        return f"{period_start.strftime('%d-%m-%Y')}–{period_end.strftime('%d-%m-%Y')}"
    return period_end.strftime("%d-%m-%Y")


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
) -> None:
    """The shared drafting core: company snapshot, org tax defaults, snapshotted lines,
    recomputed totals, one DRAFT invoice carrying ``link_field`` for idempotency."""
    org_id = ctx.org.id
    company = (
        await ctx.session.execute(
            text("SELECT id, name, invoice_email, vat_number, coc_number, address_line1,"
                 " address_line2, postal_code, city, country"
                 " FROM companies WHERE id = :cid AND org_id = :oid"),
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
                # A cycle raises recurring lines by definition — that is what a cycle is.
                "line_kind": LineKind.SUBSCRIPTION.value,
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

    customer = {
        "name": company["name"],
        "address_line1": company["address_line1"],
        "address_line2": company["address_line2"],
        "postal_code": company["postal_code"],
        "city": company["city"],
        "country": company["country"],
        "vat_number": company["vat_number"],
        "coc_number": company["coc_number"],
        "email": company["invoice_email"],
    }
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
        await lines.create(invoice_id=invoice.id, **row)
    if link_field == "subscription_id":
        # The cron's own claim on the period, in the same table a hand-built invoice writes
        # to — so "has this period been billed?" has exactly one answer to look up.
        await ctx.repo(InvoiceSubscriptionPeriod).create(
            invoice_id=invoice.id,
            subscription_id=uuid.UUID(str(link_id)),
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
    )
