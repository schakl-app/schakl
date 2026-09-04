"""The busy seam's leave third (``app/core/busy.py``): when somebody is away.

An absence is not a booking: nothing can be planned *around* a day off, so it answers as an
``away`` band over the day rather than a block beside the others. A timed absence (an afternoon
off, a type drawn per hour — #270) keeps its window, and a pending request still counts as
somebody's plan, drawn tentative.

Who is off is normal team-visible information in an agency (``LeaveService.team``'s rule), and
a ``client`` role never reaches the route that asks. The *type* of leave is the same
information the team calendar already draws for everybody holding ``leave.request.read``, so
it travels as the title under that key and stays out for anyone without it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

from sqlalchemy import select

from app.core.busy import BusyItem
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.modules.leave.models import LeaveRequestStatus, LeaveType
from app.modules.leave.service import LeaveService


def _type_label(leave_type: LeaveType | None, locale: str) -> str | None:
    if leave_type is None:
        return None
    labels = leave_type.label_i18n or {}
    return labels.get(locale) or labels.get("nl") or labels.get("en") or leave_type.key


async def leave_busy(
    ctx: RequestContext,
    user_ids: list[uuid.UUID],
    window_start: datetime,
    window_end: datetime,
) -> list[BusyItem]:
    zone = await org_zoneinfo(ctx.session, ctx.org.id)
    date_from = window_start.astimezone(zone).date()
    date_to = (window_end.astimezone(zone) - timedelta(microseconds=1)).date()
    wanted = set(user_ids)
    # The team feed's own resolution (scheduled windows, holidays, the org zone) rather than a
    # second reading of the same rows; it is bounded by the org's headcount, not by the window.
    rows = [
        item
        for item in await LeaveService(ctx).team(date_from=date_from, date_to=date_to)
        if item.user_id in wanted
    ]
    if not rows:
        return []
    detailed = ctx.can("leave.request.read")
    types: dict[uuid.UUID, LeaveType] = {}
    if detailed:
        types = {
            row.id: row
            for row in (
                await ctx.session.execute(select(LeaveType).where(LeaveType.org_id == ctx.org.id))
            )
            .scalars()
            .all()
        }
    locale = getattr(ctx, "locale", None) or "nl"
    items: list[BusyItem] = []
    for item in rows:
        title = _type_label(types.get(item.leave_type_id), locale) if detailed else None
        tentative = item.status is LeaveRequestStatus.PENDING
        ref = str(item.id) if detailed else None
        if (
            item.starts_at is not None
            and item.ends_at is not None
            and (item.start_time is not None or item.end_time is not None)
        ):
            # A timed single-day absence keeps its window: "vrij vanaf 15:00" leaves the morning.
            items.append(
                BusyItem(
                    user_id=item.user_id,
                    starts_at=item.starts_at,
                    ends_at=item.ends_at,
                    source="leave",
                    kind="away",
                    tentative=tentative,
                    title=title,
                    ref=ref,
                )
            )
            continue
        # A whole-day absence: one band per day it actually costs — a weekend or a holiday
        # inside a fortnight off is not a day anybody was going to be planned on either way,
        # but it is also not *this* absence, so it draws nothing.
        for day in item.days:
            if day.date < date_from or day.date > date_to or day.hours <= 0:
                continue
            start = datetime.combine(day.date, time.min).replace(tzinfo=zone)
            items.append(
                BusyItem(
                    user_id=item.user_id,
                    starts_at=start,
                    ends_at=start + timedelta(days=1),
                    source="leave",
                    kind="away",
                    all_day=True,
                    tentative=tentative,
                    title=title,
                    ref=ref,
                )
            )
    return items
