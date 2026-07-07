"""Tasks panel on the company detail view (CLAUDE.md §6, the modular hub).

Lists the open/in-progress tasks attached to a company.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.tasks.models import Task, TaskStatus
from app.registry import PanelSpec

_OPEN_STATUSES = (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value)


async def _tasks_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    repo = ctx.repo(Task)
    stmt = (
        repo.scoped_select()
        .where(Task.company_id == company_id)
        .where(Task.status.in_(_OPEN_STATUSES))
        .order_by(Task.due_date.asc().nulls_last(), Task.created_at.desc())
        .limit(50)
    )
    tasks = (await ctx.session.execute(stmt)).scalars().all()
    return {
        "tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "due_date": t.due_date.isoformat() if t.due_date else None,
            }
            for t in tasks
        ]
    }


tasks_company_panel = PanelSpec(
    key="tasks.company",
    entity_type="company",
    title_key="tasks.panel.title",
    provider=_tasks_provider,
    position=30,
)
