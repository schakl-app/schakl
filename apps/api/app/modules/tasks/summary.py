"""What tasks contributes to a client's vital signs (#364).

*Open taken, waarvan n over tijd.* The tasks panel already knew the open count and the page still
made the reader scroll to it and count the red dates themselves; two grouped scalars put the
answer above the fold, and the tile opens the filtered list it counted.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.modules.tasks.models import Task
from app.modules.tasks.service import TaskService
from app.modules.tasks.statuses import load_statuses, non_terminal_keys
from app.registry import SummarySpec, SummaryTile


async def _open_tasks(ctx: RequestContext, company_id: uuid.UUID) -> list[SummaryTile]:
    # The service's repository, never a bare ``ctx.repo(Task)``: a portal login's repo carries
    # ``Task.__portal_horizon_clause__`` (the client's own companies *and* the
    # ``visible_to_client`` tick), and the tile is one more reader of that count. Built on the
    # bare repo it counted the agency's whole backlog for the client — "7 open taken" above a
    # panel and a list that both showed one (#285's failure mode (2), the shape
    # ``test_portal_task_count_matches_its_list`` already pins for the panel).
    repo = TaskService(ctx).repo
    # "Open" is every non-terminal *configured* status (#62), never a fixed open/in_progress pair.
    statuses = await load_statuses(ctx.session, ctx.org.id)
    open_keys = non_terminal_keys(statuses)
    today = await org_today(ctx.session, ctx.org.id)

    base = (
        repo.scoped_count_select()
        .where(Task.company_id == company_id)
        .where(Task.status.in_(open_keys))
    )
    open_count = int(await ctx.session.scalar(base) or 0)
    if not open_count:
        return []
    overdue = int(
        await ctx.session.scalar(base.where(Task.due_date < today)) or 0
    )
    return [
        SummaryTile(
            key="tasks.open",
            label_key="companies.summary.open_tasks",
            value=str(open_count),
            format="number",
            tone="bad" if overdue else "neutral",
            hint_key="companies.summary.tasks_overdue" if overdue else None,
            hint_params={"count": overdue},
            # The count is org-wide for this client, and /tasks defaults an absent assignee to
            # the signed-in user — without saying so the tile counts 7 and opens a list of 2.
            href=f"/tasks?company_id={company_id}&assignee_user_id=all",
            position=20,
        )
    ]


tasks_company_summary = SummarySpec(
    key="tasks.company",
    entity_type="company",
    provider=_open_tasks,
    requires_permission="tasks.task.read",
    position=20,
)


__all__ = ["tasks_company_summary"]
