"""A client's tasks go with the client (docs/TRASH.md).

A to-do is not a record the way an invoice is, so it does not block; it hides while the
client is in the trash and goes when the client is purged. The purge runs through this
module rather than the ``SET NULL`` the FK would do, for two reasons: a task must have a
client now (``errors.tasks_company_required``), so a nulled task is a row nothing can ever
edit again; and a task's planned blocks are mirrored into calendars, which a cascade tells
nothing — ``remove_for_task`` is what takes the event out of somebody's Google agenda.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.core.trash import TrashDependent, count_by_column
from app.modules.tasks.models import Task
from app.modules.tasks.scheduling import TaskScheduleService


async def purge_company_tasks(ctx: RequestContext, company_id: uuid.UUID) -> None:
    # The client is in the trash, so its tasks are hidden from every ordinary repository
    # read — the trash's own door is the only way to reach them.
    repo = ctx.repo(Task, include_trashed=True)
    tasks = list(
        (await ctx.session.execute(repo.scoped_select().where(Task.company_id == company_id)))
        .scalars()
        .all()
    )
    schedules = TaskScheduleService(ctx)
    for task in tasks:
        await schedules.remove_for_task(task.id)
        await repo.delete(task)


TASK_TRASH_DEPENDENTS = (
    TrashDependent(
        key="tasks.tasks",
        label_key="trash.dependent.tasks.tasks",
        blocks=False,
        count=count_by_column("tasks", "company_id"),
        purge=purge_company_tasks,
    ),
)
