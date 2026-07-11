"""Tasks panel on the company detail view (CLAUDE.md §6, the modular hub).

The per-client task overview: open/in-progress tasks with label chips, checklist progress
and comment counts, plus the open count for the header.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.tasks.models import Task
from app.modules.tasks.service import TaskService
from app.modules.tasks.statuses import load_statuses, non_terminal_keys
from app.registry import PanelSpec


async def _tasks_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    service = TaskService(ctx)
    # "Open" is every non-terminal configured status (issue #62), not a fixed open/in_progress pair.
    statuses = await load_statuses(ctx.session, ctx.org.id)
    stmt = (
        service.repo.scoped_select()
        .where(Task.company_id == company_id)
        .where(Task.status.in_(non_terminal_keys(statuses)))
        .order_by(Task.due_date.asc().nulls_last(), Task.position.asc())
        .limit(50)
    )
    tasks = (await ctx.session.execute(stmt)).scalars().all()
    items = await service._list_items(tasks)
    return {
        "open_count": len(items),
        "tasks": [item.model_dump(mode="json") for item in items],
    }


tasks_company_panel = PanelSpec(
    key="tasks.company",
    entity_type="company",
    title_key="tasks.panel.title",
    provider=_tasks_provider,
    position=30,
)
