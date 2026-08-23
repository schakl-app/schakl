"""Time panel on the company detail view (CLAUDE.md §6, the modular hub).

Shows total minutes logged against a company plus a few recent entries (across the team).

What a row carries is a product decision, not a dump of the model (#400). The panel used to send
a description and a duration, so *"Back-up teruggezet op de testomgeving"* appeared three times
on one client with nothing to tell the three apart — three days and three colleagues, rendered
identically, on the screen somebody reads while the client is on the phone. It now carries the
day, **who** logged it, and the fields the panel's own correct-this-row dialog posts back
(``ended_at`` / ``break_minutes``), plus ``approved_at`` so the browser can hide a control the
API would refuse: signed-off hours are locked to whoever may approve them.

None of that costs a query. ``user_id`` is a column on the entry, not a join, and the entry count
rides the aggregate that was already being issued — the panel is still exactly two statements,
neither of which grows with the client's history (``tests/test_perf_query_budgets.py``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.core.tenancy import RequestContext
from app.modules.time.models import TimeEntry
from app.registry import PANEL_FEED, PROMINENCE_PRIMARY, SIZE_HALF, PanelSpec

# How many recent entries the panel shows — the hub's shared feed default (#407). The panel used
# to load the client's *entire* timesheet to display this handful and one total; the total is an
# aggregate now and the list is bounded, so ten years of history costs what a new client costs.
_RECENT = PANEL_FEED


async def _time_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    repo = ctx.repo(TimeEntry)
    totals_stmt = (
        select(
            func.coalesce(func.sum(TimeEntry.minutes), 0),
            # How many rows exist behind the ten (#400). A panel that truncates says so, and
            # saying so needs the number — asked for here rather than in a second statement,
            # because it is the same predicate over the same rows.
            func.count(),
        )
        .select_from(TimeEntry)
        .where(
            TimeEntry.org_id == ctx.org.id,
            TimeEntry.company_id == company_id,
        )
    )
    # A hand-built aggregate leaves the repository's path, so it asks for the horizon by name
    # (§15) — the count above a filtered list is exactly where it goes missing.
    horizon = repo.horizon_condition()
    if horizon is not None:
        totals_stmt = totals_stmt.where(horizon)
    total_minutes, total_entries = (await ctx.session.execute(totals_stmt)).one()

    entries = (
        (
            await ctx.session.execute(
                repo.scoped_select()
                .where(TimeEntry.company_id == company_id)
                .order_by(TimeEntry.started_at.desc())
                .limit(_RECENT)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total_minutes": int(total_minutes or 0),
        "total_entries": int(total_entries or 0),
        "recent": [
            {
                "id": str(e.id),
                "user_id": str(e.user_id),
                "description": e.description,
                "minutes": e.minutes,
                "started_at": e.started_at.isoformat(),
                "ended_at": e.ended_at.isoformat() if e.ended_at else None,
                "break_minutes": e.break_minutes,
                "billable": e.billable,
                "approved_at": e.approved_at.isoformat() if e.approved_at else None,
            }
            for e in entries
        ],
    }


time_company_panel = PanelSpec(
    key="time.company",
    entity_type="company",
    title_key="time.panel.title",
    provider=_time_provider,
    position=40,
    # What somebody worked on, for how long, and whether we bill for it (#365) — the sharpest
    # of the seven panels the hub used to hand anyone holding `companies.company.read`.
    requires_permission="time.entry.read",
    prominence=PROMINENCE_PRIMARY,
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("recent"),
)
