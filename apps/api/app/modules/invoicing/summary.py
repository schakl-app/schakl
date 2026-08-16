"""What invoicing contributes to a client's vital signs (#364).

One number: **openstaand** — what this client still owes us across every issued document, and
how much of it is past its due date. It was already on the page, spread across an invoice card
the reader had to scroll to and add up by eye; the strip is where a relationship's health is
supposed to be answerable without scrolling at all.

Aggregated in SQL, never by loading the ledger: a client with four hundred invoices costs the
same as one with four (docs/PERFORMANCE.md).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import case, func, select

from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.modules.invoicing.models import Invoice, InvoiceStatus
from app.registry import SummarySpec, SummaryTile


async def _outstanding(ctx: RequestContext, company_id: uuid.UUID) -> list[SummaryTile]:
    repo = ctx.repo(Invoice)
    today = await org_today(ctx.session, ctx.org.id)

    # `outstanding_of` in one expression: total − paid − credited + applied. Kept here rather
    # than folded into a helper because a SQL sum and a Python sum of the same rows must agree,
    # and the way to keep them agreeing is for both to name the same four columns.
    owed = (
        Invoice.total
        - Invoice.paid_total
        - func.coalesce(Invoice.credited_total, 0)
        + func.coalesce(Invoice.applied_total, 0)
    )
    stmt = select(
        func.coalesce(func.sum(owed), 0),
        func.coalesce(
            func.sum(case((Invoice.due_date < today, owed), else_=0)),
            0,
        ),
        func.count(case((Invoice.due_date < today, 1))),
        func.min(Invoice.currency),
    ).where(
        Invoice.org_id == ctx.org.id,
        Invoice.company_id == company_id,
        Invoice.status == InvoiceStatus.OPEN.value,
    )
    # A hand-built aggregate leaves the repository's path, so it asks for the horizon by name
    # (§15) — an aggregate over a filtered list is exactly where it goes missing.
    horizon = repo.horizon_condition()
    if horizon is not None:
        stmt = stmt.where(horizon)
    row = (await ctx.session.execute(stmt)).one()
    total, overdue_amount, overdue_count, currency = row

    if not total or Decimal(total) <= 0:
        # Nothing owed is not a vital sign; a tile that is always on screen saying €0 is the
        # chrome this whole strip exists to remove.
        return []
    return [
        SummaryTile(
            key="invoicing.outstanding",
            label_key="companies.summary.outstanding",
            value=str(total),
            format="money",
            currency=currency or "EUR",
            tone="bad" if overdue_amount and Decimal(overdue_amount) > 0 else "warn",
            hint_key="companies.summary.outstanding_overdue" if overdue_count else None,
            hint_params={"count": int(overdue_count or 0)},
            href=f"/invoices?company={company_id}&status=open",
            position=10,
        )
    ]


invoicing_company_summary = SummarySpec(
    key="invoicing.company",
    entity_type="company",
    provider=_outstanding,
    requires_permission="invoicing.invoice.read",
    position=10,
)
