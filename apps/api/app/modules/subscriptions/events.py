"""What a document claiming a period means for a **one-time** agreement.

An agreement with ``interval = once`` owes exactly one period, and once a document holds it
the agreement is finished — a website build that has been invoiced is not "active", the way a
retainer between two months is. The cycle cron says so itself when it drafts the invoice
(``jobs.py``); this is the other path: a period claimed by hand — a line picked in the editor,
an invoice built from the backlog — and given back when that document is deleted, cancelled or
fully credited.

`invoicing` owns the claim tables and emits ``subscription.period_claimed`` /
``subscription.period_released`` as it writes and removes rows (§6: the module that knows what
happened says so, the module that owns the agreement decides what it means). Both handlers run
in the emitter's transaction, so a claim and the completion it implies commit together.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select

from app.core.activity import ActivityService
from app.core.events import EmitContext
from app.modules.subscriptions.models import (
    Subscription,
    SubscriptionInterval,
    SubscriptionStatus,
)

logger = logging.getLogger("schakl.subscriptions")

ENTITY_TYPE = "subscription"


async def _one_time(ctx: EmitContext, payload: dict[str, Any]) -> Subscription | None:
    try:
        subscription_id = uuid.UUID(str(payload.get("subscription_id")))
    except ValueError:
        return None
    sub = await ctx.session.scalar(
        select(Subscription).where(
            Subscription.org_id == ctx.org.id, Subscription.id == subscription_id
        )
    )
    if sub is None or sub.interval != SubscriptionInterval.ONCE.value:
        return None
    return sub


async def on_period_claimed(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """A document now bills the one period a one-time agreement owes: it is completed."""
    sub = await _one_time(ctx, payload)
    if sub is None or sub.status not in (
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.PAUSED.value,
    ):
        return
    sub.status = SubscriptionStatus.COMPLETED.value
    sub.next_invoice_date = None
    await ctx.session.flush()
    await ActivityService(ctx).record(
        ENTITY_TYPE, sub.id, "completed", {"invoice_id": str(payload.get("invoice_id"))}
    )


async def on_period_released(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """The document that billed a one-time agreement let go of it: the period is owed
    again, on the boundary the claim named, and the agreement is active once more."""
    sub = await _one_time(ctx, payload)
    if sub is None or sub.status != SubscriptionStatus.COMPLETED.value:
        return
    try:
        boundary = date.fromisoformat(str(payload.get("period_end")))
    except ValueError:
        boundary = sub.start_date
    sub.status = SubscriptionStatus.ACTIVE.value
    sub.next_invoice_date = boundary
    await ctx.session.flush()
    await ActivityService(ctx).record(
        ENTITY_TYPE, sub.id, "reopened", {"invoice_id": str(payload.get("invoice_id"))}
    )
