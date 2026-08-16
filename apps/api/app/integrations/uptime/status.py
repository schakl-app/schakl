"""What ``websites`` is allowed to know about a monitor: whether the site is up (#356).

The ``/websites`` list drew a green pill from ``Website.uptime_enabled`` — a configuration flag —
and green is this app's healthy state, so a site that was down looked exactly like one that was
up. The state exists here; it just had no way across. :mod:`app.core.monitoring` is that way, and
this is the half the module owns: the query, over its own tables, resolving nothing but a status
token per website.

Two rules it exists to keep.

**A status is the *last* heartbeat, not the newest row of anything.** ``DISTINCT ON`` over the
window, ordered by ``observed_at`` descending, so a monitor that flapped up and back down reports
down. A monitor with no heartbeat yet answers ``None`` — *watched, never observed* — which the
caller must not collapse into "down": nothing has looked.

**A paused monitor makes no claim.** Kuma stops checking a paused monitor, so its last heartbeat
freezes at whatever it happened to be. Reporting that as live health would be the same lie one
layer down, so a paused monitor answers ``None`` too.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.monitoring import register_website_status_resolver
from app.core.tenancy import RequestContext
from app.integrations.uptime.models import UptimeHeartbeat, UptimeMonitor


async def website_statuses(
    ctx: RequestContext, website_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """``{website_id: "up" | "down" | … | None}`` for every watched site in the set.

    A website absent from the result has no monitor. One query for the monitors and one for
    their last heartbeats, whatever the page size — a resolver called per row is the N+1
    `docs/PERFORMANCE.md` bans, and a list is the only place this is read.
    """
    if not website_ids:
        return {}
    monitors = (
        await ctx.session.execute(
            select(UptimeMonitor.id, UptimeMonitor.website_id, UptimeMonitor.active).where(
                UptimeMonitor.org_id == ctx.org.id,
                UptimeMonitor.website_id.in_(website_ids),
            )
        )
    ).all()
    if not monitors:
        return {}

    # A website may carry more than one monitor (apex and www, http and a keyword check). The
    # *site* is down if any of them is: an answer that hid one failing check behind another
    # passing one would be worse than the flag it replaces.
    statuses: dict[uuid.UUID, str | None] = {}
    live: dict[uuid.UUID, uuid.UUID] = {}
    for monitor_id, website_id, active in monitors:
        statuses.setdefault(website_id, None)
        if active:
            live[monitor_id] = website_id

    if live:
        last = (
            await ctx.session.execute(
                select(UptimeHeartbeat.monitor_id, UptimeHeartbeat.status)
                .where(
                    UptimeHeartbeat.org_id == ctx.org.id,
                    UptimeHeartbeat.monitor_id.in_(live),
                )
                .distinct(UptimeHeartbeat.monitor_id)
                .order_by(UptimeHeartbeat.monitor_id, UptimeHeartbeat.observed_at.desc())
            )
        ).all()
        for monitor_id, status in last:
            website_id = live[monitor_id]
            current = statuses.get(website_id)
            statuses[website_id] = _worst(current, status)
    return statuses


#: Worst-first, so one failing check is never hidden behind a passing one. Anything unrecognised
#: sorts as the least severe known state rather than being invented a rank.
_SEVERITY = {"down": 3, "pending": 2, "maintenance": 1, "up": 0}


def _worst(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    return candidate if _SEVERITY.get(candidate, 0) > _SEVERITY.get(current, 0) else current


register_website_status_resolver(website_statuses)
