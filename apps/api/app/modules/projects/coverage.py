"""React to a project being linked to a subscription (issue #284).

Work a retainer already pays for is not separately invoiceable, so a project that is covered
by an agreement — or created for one — stops seeding *billable* time entries: its
``billable_default`` is cleared the moment the link is made.

It stays a **default**, not a lock. The tenant can tick the project back to billable (extra
work agreed outside the retainer), and the flag on any single entry is always the logger's
call; only the value the form and the API start from moves. Re-saving an agreement with the
same links changes nothing — the subscriptions module announces newly-added links only.

Cross-module reaction over the bus, never an import of the subscriptions module's internals
(CLAUDE.md §6); the handler runs in the linking request's transaction, so the link and the
project it repriced commit together.
"""

from __future__ import annotations

from typing import Any

from app.core.activity.service import ActivityService
from app.core.events import EmitContext
from app.modules.projects.models import Project

ENTITY_TYPE = "project"


async def on_subscription_project_linked(ctx: EmitContext, payload: dict[str, Any]) -> None:
    repo = ctx.repo(Project)
    activity = ActivityService(ctx)
    for project_id in payload.get("project_ids") or []:
        project = await repo.get(project_id)
        # A project the subscription named but that has since gone is not an error here: the
        # link write already validated the target, and this reaction owns nothing.
        if project is None or not project.billable_default:
            continue
        await repo.update(project, billable_default=False)
        # On the project's own trail (§16), so "why did this stop being billable?" is
        # answerable from the project rather than from the agreement that caused it.
        await activity.record_update(
            ENTITY_TYPE,
            project.id,
            {"billable_default": True},
            {"billable_default": False},
        )
