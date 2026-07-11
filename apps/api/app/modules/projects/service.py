"""Business logic for projects — all DB access via the tenant-scoped repository (CLAUDE.md §6).

Customizable entity: ``custom`` is validated against the tenant's definitions on every write.

Several employees work a project: ``project_assignees`` holds them all, one starred as primary.
``projects.responsible_user_id`` mirrors that primary and is dropped in a later release
(docs/WORKFLOW.md, expand/contract) — read ``primary_assignee()`` instead of the column.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.assignees import AssigneeService
from app.core.auth.models import User
from app.core.customfields import CustomFieldsService
from app.core.events import emit
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.modules.projects.budget import period_bound, period_start_date
from app.modules.projects.models import Project, ProjectAssignee, ProjectStatus
from app.modules.projects.schemas import ProjectCreate, ProjectUpdate
from app.schemas import BudgetHours

ENTITY_TYPE = "project"

# Definition fields whose before/after values the activity trail records (issue #67); the
# freeform description and the custom JSONB are left out of the diff.
_AUDITED_FIELDS = (
    "name",
    "status",
    "company_id",
    "responsible_user_id",
    "budget_hours",
    "budget_amount",
    "hourly_rate",
    "budget_period",
    "start_date",
    "end_date",
)


def _primary_assignee_name() -> Any:
    """Sort key for "assigned employee" — the primary assignee's display name.

    Correlated, not joined: a project has many assignees and a join would multiply its row.
    Falls back to email like the UI does; no primary sorts last (see ``apply_sort``).
    """
    return (
        select(func.lower(func.coalesce(User.full_name, User.email)))
        .select_from(ProjectAssignee)
        .join(User, User.id == ProjectAssignee.user_id)
        .where(
            ProjectAssignee.project_id == Project.id,
            ProjectAssignee.org_id == Project.org_id,
            ProjectAssignee.is_primary.is_(True),
        )
        .correlate(Project)
        .scalar_subquery()
    )


# Columns a client may sort by. The value comes from the URL, so anything not named here is
# rejected rather than reaching the query (app/core/sorting.py).
SORTABLE = {
    # Case-insensitive, or Postgres' default collation files lowercase names after uppercase.
    "name": func.lower(Project.name),
    "status": Project.status,
    "assignee": _primary_assignee_name(),
    "start_date": Project.start_date,
    "end_date": Project.end_date,
    "budget_hours": Project.budget_hours,
    "created_at": Project.created_at,
    "updated_at": Project.updated_at,
}


def _hours(minutes: int) -> float:
    return round(minutes / 60, 2)


@dataclass(frozen=True)
class BudgetedProject:
    """An active project with an hour budget — what a client's roll-up is made of (#25).

    Carries its own ``period_start`` already resolved, so `companies` can roll these up without
    knowing how a budget period turns into an instant (that rule is ours, in `budget.py`).
    """

    id: uuid.UUID
    company_id: uuid.UUID
    budget_hours: float
    budget_period: str
    period_start: datetime


class ProjectService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Project)
        self.custom_fields = CustomFieldsService(ctx)
        self.assignees = AssigneeService(ctx, ProjectAssignee, "project_id")

    async def _attach_assignees(self, projects: Sequence[Project]) -> None:
        """One extra query for the whole page, never one per row (docs/PERFORMANCE.md)."""
        if not projects:
            return
        grouped = await self.assignees.for_entities([p.id for p in projects])
        for project in projects:
            project.assignees = grouped.get(project.id, [])

    async def _attach_hours(self, projects: Sequence[Project]) -> None:
        """Budget burn for the page, in one grouped query. Only runs when the column is visible.

        The time module is reached through its published service, imported here rather than at
        module scope: nothing outside this branch should drag `time` in, and a module must never
        import another's internals (CLAUDE.md §6).
        """
        if not projects:
            return
        from app.modules.time.service import LoggedMinutes, TimeService

        periods = {p.id: period_bound(p.budget_period) for p in projects}
        logged = await TimeService(self.ctx).minutes_by_project(periods)
        for project in projects:
            minutes = logged.get(project.id, LoggedMinutes())
            budget = float(project.budget_hours) if project.budget_hours is not None else None
            spent = _hours(minutes.total)
            project.hours = BudgetHours(
                period=project.budget_period,
                # The local day the period began — what a client sends back as `date_from` to list
                # the entries behind this number (#43). Never the UTC instant's `.date()`.
                period_start=period_start_date(project.budget_period),
                budget_hours=budget,
                spent_hours=spent,
                billable_hours=_hours(minutes.billable),
                unapproved_hours=_hours(minutes.unapproved),
                # Deliberately unclamped: an over-budget project reports a negative remainder.
                remaining_hours=round(budget - spent, 2) if budget is not None else None,
            )

    async def budgeted_active_for_companies(
        self, company_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[BudgetedProject]]:
        """The active, budgeted projects of each client — the only projects a client's remaining
        hours can be rolled up from. Published so `companies` never imports our models (§6)."""
        if not company_ids:
            return {}
        stmt = select(
            Project.id, Project.company_id, Project.budget_hours, Project.budget_period
        ).where(
            Project.org_id == self.ctx.org.id,
            Project.company_id.in_(company_ids),
            Project.status == ProjectStatus.ACTIVE.value,
            Project.budget_hours.is_not(None),
        )
        grouped: dict[uuid.UUID, list[BudgetedProject]] = {cid: [] for cid in company_ids}
        for row in (await self.ctx.session.execute(stmt)).all():
            grouped[row[1]].append(
                BudgetedProject(
                    id=row[0],
                    company_id=row[1],
                    budget_hours=float(row[2]),
                    budget_period=row[3],
                    period_start=period_bound(row[3]),
                )
            )
        return grouped

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        status: ProjectStatus | None = None,
        q: str | None = None,
        mine: bool = False,
        sort: str | None = None,
        hours: bool = False,
        count: bool = True,
    ) -> tuple[Sequence[Project], int]:
        conditions = []
        if company_id is not None:
            conditions.append(Project.company_id == company_id)
        if status is not None:
            conditions.append(Project.status == status.value)
        if q:
            conditions.append(Project.name.ilike(f"%{q.strip()}%"))
        if mine:
            # "My projects" matches *any* assignee, not just the primary.
            conditions.append(
                Project.id.in_(self.assignees.entity_ids_for_user(self.ctx.user.id))
            )
        stmt = apply_sort(
            self.repo.scoped_select().where(*conditions),
            sort,
            SORTABLE,
            default=Project.name.asc(),
        ).limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # ``count=False`` skips the discarded COUNT(*) for name-only lookups.
        total = (
            int(
                await self.ctx.session.scalar(
                    select(func.count())
                    .select_from(Project)
                    .where(Project.org_id == self.ctx.org.id, *conditions)
                )
                or 0
            )
            if count
            else len(items)
        )
        await self._attach_assignees(items)
        if hours:
            await self._attach_hours(items)
        return items, total

    async def get(self, project_id: uuid.UUID, *, hours: bool = False) -> Project:
        project = await self.repo.get_or_404(project_id)
        await self._attach_assignees([project])
        # Opt-in, exactly as on the list. The detail page asks for it because its budget bar and
        # its Uren panel must both count from the *same* period start (#43) — one the API resolves
        # in Europe/Amsterdam (budget.py), which a browser recomputing it in UTC gets wrong twice
        # a year.
        if hours:
            await self._attach_hours([project])
        return project

    async def primary_assignee(self, project_id: uuid.UUID) -> uuid.UUID | None:
        """Who owns this project. Published so other modules never import our models (§6)."""
        return await self.assignees.primary(project_id)

    async def create(self, data: ProjectCreate) -> Project:
        self.ctx.require("projects.project.write")
        values = data.model_dump()
        values.pop("assignees", None)

        # A project inherits the client's *primary* when nobody was named — not the client's whole
        # roster, which is a superset of the people actually on this project.
        fallback = data.responsible_user_id
        if data.assignees is None and fallback is None and values.get("company_id") is not None:
            fallback = await self._company_primary(values["company_id"])
        links = self.assignees.normalize(data.assignees, fallback_primary=fallback)
        values["responsible_user_id"] = self.assignees.primary_of(links)

        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        project = await self.repo.create(**values)
        await self.assignees.replace(project.id, links)
        project.assignees = await self.assignees.for_entity(project.id)
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, project.id)
        # Projects have no "created" event, so this is the roster's only signal (issue #16).
        if project.assignees:
            await self._emit_project(
                "project.assigned", project, [a.user_id for a in project.assignees]
            )
        return project

    async def _emit_project(
        self,
        event: str,
        project: Project,
        recipients: Sequence[uuid.UUID],
        params: dict | None = None,
    ) -> None:
        """Announce a project change on the bus (CLAUDE.md §6 — never a cross-module import).

        We name our own audience; notifications adds watchers, drops the actor and the muted,
        and applies each recipient's preference. ``title`` is snapshotted for the feed.
        """
        payload: dict = {
            "project_id": project.id,
            "title": project.name,
            "_recipients": list(recipients),
        }
        payload.update(params or {})
        await emit(event, self.ctx, payload)

    async def _company_primary(self, company_id: uuid.UUID) -> uuid.UUID | None:
        """The primary assignee of a company, via its published service (§6 — no model
        cross-imports). ``None`` when the client has nobody assigned."""
        from app.modules.companies.service import CompanyService

        return await CompanyService(self.ctx).primary_assignee(company_id)

    async def update(self, project_id: uuid.UUID, data: ProjectUpdate) -> Project:
        self.ctx.require("projects.project.write")
        project = await self.repo.get_or_404(project_id)
        previous_status = project.status
        before_fields = snapshot(project, _AUDITED_FIELDS)
        values = data.model_dump(exclude_unset=True)
        # ``replace`` is delete-then-insert, so who is *new* has to be read before the write.
        roster_touched = "assignees" in values or "responsible_user_id" in values
        before: set[uuid.UUID] = (
            {a.user_id for a in await self.assignees.for_entity(project_id)}
            if roster_touched
            else set()
        )
        if "custom" in values:
            values["custom"] = await self.custom_fields.validate(
                ENTITY_TYPE, values.get("custom") or {}
            )

        # Sending ``assignees`` replaces the roster wholesale. Sending only ``responsible_user_id``
        # just moves the star — the other assignees stay put.
        links = None
        if "assignees" in values:
            values.pop("assignees")
            links = self.assignees.normalize(
                data.assignees, fallback_primary=values.get("responsible_user_id")
            )
            values["responsible_user_id"] = self.assignees.primary_of(links)

        project = await self.repo.update(project, **values)
        if links is not None:
            await self.assignees.replace(project.id, links)
        elif "responsible_user_id" in values:
            await self.assignees.set_primary(project.id, values["responsible_user_id"])
        project.assignees = await self.assignees.for_entity(project.id)
        after = {a.user_id for a in project.assignees}
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, project.id, before_fields, snapshot(project, _AUDITED_FIELDS)
        )

        if project.status != previous_status:
            await self._emit_project(
                "project.status_changed",
                project,
                sorted(after),
                {"from": previous_status, "to": project.status},
            )
        # Only a request that touched the roster can add anyone; ``before`` is deliberately
        # empty otherwise, so the diff would otherwise re-announce the whole roster.
        if roster_touched and (added := after - before):
            await self._emit_project("project.assigned", project, sorted(added))
        return project

    async def delete(self, project_id: uuid.UUID) -> None:
        self.ctx.require("projects.project.delete")
        project = await self.repo.get_or_404(project_id)
        await self.repo.delete(project)
