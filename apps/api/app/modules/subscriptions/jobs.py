"""Invoice-cycle cron (issue #30): advance ``next_invoice_date`` and emit ``subscription.due``.

Runs daily, per org via ``run_per_org``. The event carries everything the consumer (#31's
accounting integration, or an #27 automation) needs to raise an invoice — the priced amount at
the period boundary, the lines, the period — because this module deliberately owns the
*agreement*, never the invoice.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext, emit
from app.core.jobs import run_per_org
from app.core.models import Org
from app.core.timezone import org_zoneinfo
from app.modules.subscriptions.models import (
    Subscription,
    SubscriptionLine,
    SubscriptionPrice,
    SubscriptionStatus,
)
from app.modules.subscriptions.service import add_months, period_months

logger = logging.getLogger("schakl.subscriptions")


async def _advance_org(org: Org, session: AsyncSession) -> None:
    today = datetime.now(await org_zoneinfo(session, org.id)).date()
    due = (
        (
            await session.execute(
                select(Subscription).where(
                    Subscription.org_id == org.id,
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.next_invoice_date.is_not(None),
                    Subscription.next_invoice_date <= today,
                )
            )
        )
        .scalars()
        .all()
    )
    ctx = SystemContext(org=org, session=session)
    for sub in due:
        invoice_date = sub.next_invoice_date
        months = period_months(sub.interval, sub.interval_count)
        # The price valid at the invoice date — history answers, current state never reprices.
        amount = await session.scalar(
            select(SubscriptionPrice.amount)
            .where(
                SubscriptionPrice.org_id == org.id,
                SubscriptionPrice.subscription_id == sub.id,
                SubscriptionPrice.valid_from <= invoice_date,
            )
            .order_by(SubscriptionPrice.valid_from.desc())
            .limit(1)
        )
        lines = (
            (
                await session.execute(
                    select(SubscriptionLine)
                    .where(
                        SubscriptionLine.org_id == org.id,
                        SubscriptionLine.subscription_id == sub.id,
                    )
                    .order_by(SubscriptionLine.position)
                )
            )
            .scalars()
            .all()
        )
        await emit(
            "subscription.due",
            ctx,
            {
                "subscription_id": sub.id,
                "company_id": sub.company_id,
                "name": sub.name,
                "amount": str(amount) if amount is not None else None,
                "currency": sub.currency,
                "period_start": add_months(invoice_date, -months).isoformat(),
                "period_end": invoice_date.isoformat(),
                "lines": [
                    {
                        "description": line.description,
                        "quantity": str(line.quantity),
                        "unit_amount": str(line.unit_amount),
                    }
                    for line in lines
                ],
            },
        )
        next_date = add_months(invoice_date, months)
        # A cycle past the agreed end has nothing left to invoice.
        sub.next_invoice_date = (
            None if sub.end_date is not None and next_date > sub.end_date else next_date
        )
    if due:
        logger.info("advanced %s due subscriptions in org %s", len(due), org.slug)


async def advance_subscriptions(ctx: dict) -> None:
    """ARQ entrypoint: fire ``subscription.due`` and roll the cycle forward, per org."""
    await run_per_org(_advance_org)
