"""What a selection of agreements can be changed to in one go (CLAUDE.md §17's pattern).

Status, category and client. The list already had a hand-written bulk status control (#153);
this is that control generalised, so the same selection can also be recategorised or moved to
the client that took over the account, and the page stops owning its own batch endpoint.

The money is not here on purpose. Price, interval and the next invoice date each decide what
somebody gets billed and when: a shared value across a selection would be wrong on most of it,
and ``update`` appends to the **price history** rather than mutating it, so a misfired bulk
price is a permanent line in the record of what this client was charged. The rate-card change
people actually want already exists and knows about proration — Prijsverhoging (#153).
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor, BulkField
from app.core.tenancy import RequestContext
from app.modules.subscriptions.impex import SUBSCRIPTION_IMPEX
from app.modules.subscriptions.models import Subscription
from app.modules.subscriptions.service import SubscriptionService


async def _delete(ctx: RequestContext, subscription: Any) -> None:
    await SubscriptionService(ctx).delete(subscription.id)


SUBSCRIPTION_BULK = BulkDescriptor(
    impex=SUBSCRIPTION_IMPEX,
    model=Subscription,
    editable=(
        BulkField("status"),
        BulkField("type"),
        BulkField("company"),
    ),
    delete_permission="subscriptions.subscription.delete",
    delete_row=_delete,
)
