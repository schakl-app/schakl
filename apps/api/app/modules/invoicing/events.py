"""``subscription.due`` → draft invoice (issue #207, closing the loop #30 left open).

The subscriptions cron owns the *agreement* and deliberately raises no invoice; it emits
``subscription.due`` with everything a consumer needs (amount, currency, period, lines).
This handler is that consumer: one **draft** per (subscription, period), idempotent both
ways — a lookup first, and the partial unique index on ``invoices`` as the backstop — so a
re-run, a crash-resume or a double emit can never double-bill a client (#31's hard rule).

It runs on the emitter's context (the cron's ``SystemContext``): no permission check — an
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
from app.modules.invoicing.calc import LineInput, compute_totals, line_amount
from app.modules.invoicing.models import (
    Invoice,
    InvoiceLine,
    InvoicingSettings,
    TaxRate,
)
from app.modules.invoicing.service import tax_label

logger = logging.getLogger("schakl.invoicing")


def _decimal(value: Any, fallback: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(fallback)


async def on_subscription_due(ctx: EmitContext, payload: dict[str, Any]) -> None:
    org_id = ctx.org.id
    subscription_id = payload.get("subscription_id")
    company_id = payload.get("company_id")
    try:
        period_end = date.fromisoformat(str(payload.get("period_end")))
        period_start = (
            date.fromisoformat(str(payload["period_start"]))
            if payload.get("period_start")
            else None
        )
    except ValueError:
        logger.warning("subscription.due with unparsable period in org %s", ctx.org.slug)
        return
    if not (subscription_id and company_id):
        return

    # Idempotency, part one: the cheap lookup (the unique index is part two).
    existing = await ctx.session.scalar(
        select(Invoice.id).where(
            Invoice.org_id == org_id,
            Invoice.subscription_id == subscription_id,
            Invoice.period_end == period_end,
        )
    )
    if existing is not None:
        return

    company = (
        await ctx.session.execute(
            text("SELECT id, name, invoice_email, vat_number, coc_number, address_line1,"
                 " address_line2, postal_code, city, country"
                 " FROM companies WHERE id = :cid AND org_id = :oid"),
            {"cid": company_id, "oid": org_id},
        )
    ).mappings().first()
    if company is None:  # the agreement outlived its client — nothing to bill
        logger.warning("subscription.due for unknown company %s in org %s", company_id, org_id)
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

    period = (
        f"{period_start.strftime('%d-%m-%Y')}–{period_end.strftime('%d-%m-%Y')}"
        if period_start
        else period_end.strftime("%d-%m-%Y")
    )
    raw_lines = payload.get("lines") or []
    if not raw_lines:
        raw_lines = [
            {
                "description": f"{payload.get('name', '')} ({period})".strip(),
                "quantity": "1",
                "unit_amount": payload.get("amount") or "0",
            }
        ]

    line_rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_lines):
        quantity = _decimal(raw.get("quantity"), "1")
        unit_price = _decimal(raw.get("unit_amount"))
        line_rows.append(
            {
                "position": index,
                "description": (raw.get("description") or payload.get("name") or "")[:512]
                or "—",
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
        currency=(payload.get("currency") or "EUR").upper(),
        locale=locale,
        intro=None,
        notes=None,
        template_id=settings_row.default_template_id if settings_row else None,
        prices_include_tax=include_tax,
        subscription_id=uuid.UUID(str(subscription_id)),
        period_start=period_start,
        period_end=period_end,
        reference=payload.get("name"),
        subtotal=totals.subtotal,
        tax_total=totals.tax_total,
        total=totals.total,
    )
    lines = ctx.repo(InvoiceLine)
    for row in line_rows:
        await lines.create(invoice_id=invoice.id, **row)
    await ActivityService(ctx).record_created(
        "invoice",
        invoice.id,
        {"subscription_id": str(subscription_id), "period_end": str(period_end)},
    )
    logger.info(
        "drafted invoice for subscription %s period %s in org %s",
        subscription_id, period_end, ctx.org.slug,
    )
