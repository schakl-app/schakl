"""What contactmomenten contribute to a client's vital signs (#364).

*Laatst gesproken.* The one number on the strip that is about the **relationship** rather than the
work: a client nobody has spoken to in four months is the thing an account manager most wants to
be told without asking, and it was previously visible only by opening a panel and reading a date.

The tone escalates with silence, which is what makes it a vital sign rather than a fact — but the
thresholds are deliberately generous (six weeks, three months): an agency that speaks to a client
quarterly by arrangement should not open a red screen every day.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.core.tenancy import RequestContext
from app.core.timezone import org_today, org_zoneinfo
from app.modules.interactions.models import Interaction
from app.registry import SummarySpec, SummaryTile

#: Days of silence before the tile warns, and before it reads as a problem.
_WARN_AFTER = 42
_BAD_AFTER = 90


async def _last_contact(ctx: RequestContext, company_id: uuid.UUID) -> list[SummaryTile]:
    repo = ctx.repo(Interaction)
    stmt = select(func.max(Interaction.occurred_at)).where(
        Interaction.org_id == ctx.org.id,
        Interaction.company_id == company_id,
    )
    horizon = repo.horizon_condition()
    if horizon is not None:
        stmt = stmt.where(horizon)
    last = await ctx.session.scalar(stmt)
    if last is None:
        # Never spoken to is the empty panel's job to say, with a ＋ beside it. A tile reading
        # "nooit" would be a permanent accusation on every client that came in by e-mail.
        return []

    zone = await org_zoneinfo(ctx.session, ctx.org.id)
    today = await org_today(ctx.session, ctx.org.id)
    # The *local* day it happened on: a call at 00:30 CEST is stored as 22:30 the day before.
    occurred_on = last.astimezone(zone).date()
    days = (today - occurred_on).days
    return [
        SummaryTile(
            key="interactions.last",
            label_key="companies.summary.last_contact",
            value=occurred_on.isoformat(),
            format="date",
            tone="bad" if days >= _BAD_AFTER else "warn" if days >= _WARN_AFTER else "neutral",
            hint_key="companies.summary.days_ago",
            hint_params={"count": max(days, 0)},
            href=f"/interactions?company_id={company_id}",
            position=40,
        )
    ]


interactions_company_summary = SummarySpec(
    key="interactions.company",
    entity_type="company",
    provider=_last_contact,
    requires_permission="interactions.interaction.read",
    position=40,
)
