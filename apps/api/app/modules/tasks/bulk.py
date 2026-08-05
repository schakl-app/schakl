"""What a selection of tasks can be changed to in one go (CLAUDE.md §17's pattern).

The list this matters most on. Triage *is* a bulk gesture — hand a sprint to a colleague, move
a run of tickets onto the project they turned out to belong to, push a week's deadlines, close
what is done — and doing it a row at a time is why it does not get done.

This is also the one module that needs its **own** writer rather than the import's. The task
import is create-only (task titles legitimately repeat, so there is no honest column to upsert
on) and its ``update_row`` is an unreachable ``NotImplementedError``; a bulk edit has the ids,
so it has no such problem. It still goes through ``TaskService.update``, which is what carries
the three rules a batch must not skip:

* the **per-row** ``:own``/``:any`` refinement (CLAUDE.md §15) — ``tasks.task.write`` is the
  only scoped write among these entities, so a member holding ``:own`` moves their own tasks
  and is refused on a colleague's, one row at a time, in the middle of the same batch;
* **a due date moved later needs a reason**, and a batch cannot invent one — those rows come
  back in ``failed`` with ``errors.due_reason_required``, which is the honest answer;
* **a status flagged ``requires_interaction`` needs its contact moment** (#157), same story.

All three surface as reported rows rather than a refused batch, because each is a fact about
*that* task and the other forty-nine are fine.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor, BulkField
from app.core.tenancy import RequestContext
from app.modules.tasks.impex import TASK_IMPEX
from app.modules.tasks.models import Task, TaskPriority
from app.modules.tasks.schemas import TaskUpdate
from app.modules.tasks.service import TaskService

#: Columns whose resolved value goes straight into ``TaskUpdate`` under its own target name.
_FIELDS = ("company_id", "project_id", "assignee_user_id", "due_date", "status")


async def _update(ctx: RequestContext, task: Any, values: dict[str, Any]) -> None:
    fields: dict[str, Any] = {key: values[key] for key in _FIELDS if key in values}
    if values.get("priority"):
        fields["priority"] = TaskPriority(values["priority"])
    if fields:
        await TaskService(ctx).update(task.id, TaskUpdate(**fields))


async def _delete(ctx: RequestContext, task: Any) -> None:
    await TaskService(ctx).delete(task.id)


TASK_BULK = BulkDescriptor(
    impex=TASK_IMPEX,
    model=Task,
    editable=(
        BulkField("status"),
        BulkField("assignee"),
        BulkField("priority"),
        BulkField("project"),
        BulkField("company"),
        BulkField("due_date"),
    ),
    delete_permission="tasks.task.delete",
    delete_row=_delete,
    update_row=_update,
)
