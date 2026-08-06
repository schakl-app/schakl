"""What a selection of projects can be changed to in one go (CLAUDE.md §17's pattern).

Status, client and the billable default: the three that describe *how a batch of work is run*
rather than what any one project is. Closing out a quarter's finished projects, moving a set
to the client that took the account over, and flipping a run of internal work to non-billable
are all one gesture here and a dozen visits to the form otherwise.

Budgets are pointedly absent. A budget is a number agreed per project, and the service already
refuses to change one a subscription sources (``errors.projects_budget_hours_locked``) — a
control that set the same figure on eight projects would be wrong on at least seven.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor, BulkField
from app.core.tenancy import RequestContext
from app.modules.projects.impex import PROJECT_IMPEX
from app.modules.projects.models import Project
from app.modules.projects.service import ProjectService


async def _delete(ctx: RequestContext, project: Any) -> None:
    await ProjectService(ctx).delete(project.id)


PROJECT_BULK = BulkDescriptor(
    impex=PROJECT_IMPEX,
    model=Project,
    editable=(
        BulkField("status"),
        BulkField("company"),
        BulkField("billable_default"),
    ),
    delete_permission="projects.project.delete",
    delete_row=_delete,
)
