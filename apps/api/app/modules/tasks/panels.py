"""Tasks panel on the company detail view (CLAUDE.md §6, the modular hub).

The per-client task overview: open/in-progress tasks with label chips, checklist progress
and comment counts, plus the open count for the header.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import and_, func, or_, select

from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.modules.tasks.models import Task
from app.modules.tasks.service import TASK_WEEK_DAYS, TaskService
from app.modules.tasks.statuses import load_statuses, non_terminal_keys
from app.registry import PANEL_ROWS, PROMINENCE_PRIMARY, SIZE_HALF, PanelSpec

#: The register default (#407). Fifty was never a considered number — it predates this panel
#: having a footer link at all, so a client with fifty open tasks drew fifty rows above the
#: client's own phone number. The whole count still rides beside them as ``open_count``.
_SHOWN = PANEL_ROWS


async def _tasks_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    service = TaskService(ctx)
    # "Open" is every non-terminal configured status (issue #62), not a fixed open/in_progress pair.
    statuses = await load_statuses(ctx.session, ctx.org.id)
    open_keys = non_terminal_keys(statuses)
    stmt = (
        service.repo.scoped_select()
        .where(Task.company_id == company_id)
        .where(Task.status.in_(open_keys))
        .order_by(Task.due_date.asc().nulls_last(), Task.position.asc())
        .limit(_SHOWN)
    )
    tasks = (await ctx.session.execute(stmt)).scalars().all()
    items = await service._list_items(tasks)
    # The counts are counted, not measured off the truncated page: ``len(items)`` reported
    # "50 open" for a client with 300, which is a wrong number rather than a rounded one
    # (docs/PERFORMANCE.md — a truncated count is a lie). Four bucket counts beside the total
    # (#397's rule on the client page): the panel partitions its rows by urgency, and a heading
    # drawn on how many of its rows landed on this page would be a second wrong number. One
    # statement, ``count(*) FILTER`` per bucket, over the same scoped relation as the list —
    # the same shape ``dashboard_mine`` uses, so the panel and the tile cannot disagree.
    today = await org_today(ctx.session, ctx.org.id)
    week_end = today + timedelta(days=TASK_WEEK_DAYS)
    visible = (
        service.repo.scoped_select()
        .where(Task.company_id == company_id)
        .where(Task.status.in_(open_keys))
        .subquery()
    )
    counts = (
        await ctx.session.execute(
            select(
                func.count(),
                func.count().filter(visible.c.due_date < today),
                func.count().filter(visible.c.due_date == today),
                func.count().filter(
                    and_(visible.c.due_date > today, visible.c.due_date <= week_end)
                ),
                func.count().filter(
                    or_(visible.c.due_date.is_(None), visible.c.due_date > week_end)
                ),
            ).select_from(visible)
        )
    ).one()
    return {
        "open_count": int(counts[0]),
        "overdue": int(counts[1]),
        "due_today": int(counts[2]),
        "due_week": int(counts[3]),
        "later": int(counts[4]),
        "tasks": [item.model_dump(mode="json") for item in items],
    }


tasks_company_panel = PanelSpec(
    key="tasks.company",
    entity_type="company",
    title_key="tasks.panel.title",
    provider=_tasks_provider,
    position=30,
    requires_permission="tasks.task.read",
    # The working surface the page did not have (#364): what we owe this client.
    prominence=PROMINENCE_PRIMARY,
    # Half width: five task rows do not want 1150 px, and full width broke the halves run so
    # every neighbour below it sat alone in a two-column row.
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("tasks"),
)
