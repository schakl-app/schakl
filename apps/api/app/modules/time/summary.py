"""What time tracking contributes to a client's vital signs (#364).

*Uren deze maand.* The month is the org's own calendar month (§8, `org_today`) — a client's hours
are a wall-clock question and UTC answers it wrong for several hours a day, and for the whole of
the first and last day of every month.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import func, select

from app.core.tenancy import RequestContext
from app.core.timezone import org_today, org_zoneinfo
from app.modules.time.models import TimeEntry
from app.registry import SummarySpec, SummaryTile


async def _hours_this_month(ctx: RequestContext, company_id: uuid.UUID) -> list[SummaryTile]:
    repo = ctx.repo(TimeEntry)
    today = await org_today(ctx.session, ctx.org.id)
    zone = await org_zoneinfo(ctx.session, ctx.org.id)
    # A stored instant is UTC; the month boundary is local. Comparing the column against a local
    # date would put the first evening of the month in the previous one for half of Europe.
    month_start = datetime.combine(today.replace(day=1), time.min, tzinfo=zone)

    stmt = select(func.coalesce(func.sum(TimeEntry.minutes), 0)).where(
        TimeEntry.org_id == ctx.org.id,
        TimeEntry.company_id == company_id,
        TimeEntry.started_at >= month_start,
    )
    horizon = repo.horizon_condition()
    if horizon is not None:
        stmt = stmt.where(horizon)
    minutes = int(await ctx.session.scalar(stmt) or 0)
    if not minutes:
        return []
    return [
        SummaryTile(
            key="time.month",
            label_key="companies.summary.hours_month",
            # Hours to two decimals, raw: the reader's locale decides the separator (§8).
            value=f"{minutes / 60:.2f}",
            format="hours",
            # The figure is the client's month, so it opens the hours report filtered to the
            # client — /time is the personal timesheet, where ?company= only prefills the entry
            # form. The report is manager-gated, so a viewer without it gets a plain tile
            # rather than a link that refuses (#253); /overview already defaults to this month.
            href=(
                f"/overview?company_id={company_id}"
                if ctx.can("time.report.read")
                else None
            ),
            position=30,
        )
    ]


time_company_summary = SummarySpec(
    key="time.company",
    entity_type="company",
    provider=_hours_this_month,
    requires_permission="time.entry.read",
    position=30,
)
