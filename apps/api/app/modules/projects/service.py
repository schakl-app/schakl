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
from zoneinfo import ZoneInfo

from sqlalchemy import bindparam, func, select, text

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.assignees import AssigneeService
from app.core.auth.models import User
from app.core.customfields import CustomFieldsService
from app.core.events import emit
from app.core.parent import ensure_parent_in_tenant
from app.core.richtext import sanitize_markdown
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.projects.budget import effective_budget, period_bound, period_start_date
from app.modules.projects.models import (
    Project,
    ProjectAssignee,
    ProjectSettings,
    ProjectStatus,
)
from app.modules.projects.schemas import (
    DashboardBudgetProject,
    DashboardBudgets,
    ProjectCreate,
    ProjectSettingsRead,
    ProjectSettingsUpdate,
    ProjectUpdate,
)
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
    "budget_period",
    "billable_default",
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
        self._tz: ZoneInfo | None = None

    async def _zone(self) -> ZoneInfo:
        """This org's zone, resolved once per service instance (CLAUDE.md §8).

        A budget period is a *local* calendar month, so the boundary depends on whose calendar —
        and that is `org_settings.timezone`, never a hardcoded city. Cached because a page of
        projects asks for the same answer once per row otherwise (docs/PERFORMANCE.md).
        """
        if self._tz is None:
            self._tz = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        return self._tz

    async def _attach_assignees(self, projects: Sequence[Project]) -> None:
        """One extra query for the whole page, never one per row (docs/PERFORMANCE.md)."""
        if not projects:
            return
        grouped = await self.assignees.for_entities([p.id for p in projects])
        for project in projects:
            project.assignees = grouped.get(project.id, [])

    async def _attach_company_names(self, projects: Sequence[Project]) -> None:
        """The client's name for the page, in one query over its distinct clients.

        Read as a bare column select rather than through the companies module's models: a module
        never imports another's internals (CLAUDE.md §6), which is why `hosting` and `domains`
        both do exactly this. It is a *label* lookup, not a horizon decision — the ids come from
        rows this caller has already been allowed to read, so the company horizon has had its
        say before we get here.
        """
        ids = {p.company_id for p in projects if p.company_id is not None}
        names: dict[uuid.UUID, str] = {}
        if ids:
            stmt = text(
                "SELECT id, name FROM companies WHERE org_id = :oid AND id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            rows = (
                await self.ctx.session.execute(stmt, {"oid": self.ctx.org.id, "ids": list(ids)})
            ).all()
            names = {row[0]: row[1] for row in rows}
        for project in projects:
            project.company_name = (  # type: ignore[attr-defined]
                names.get(project.company_id) if project.company_id else None
            )

    async def _attach_subscription_sources(self, projects: Sequence[Project]) -> dict:
        """The active subscriptions each project's hours derive from (issue #225), attached as
        ``budget_sources``. One grouped query, via the subscriptions module's published service
        — imported lazily like `time`, never its internals (CLAUDE.md §6).
        """
        if not projects:
            return {}
        from app.modules.subscriptions.service import SubscriptionService

        sources = await SubscriptionService(self.ctx).hours_for_projects(
            [p.id for p in projects]
        )
        for project in projects:
            project.budget_sources = sources.get(project.id, [])
        return sources

    async def _attach_hours(self, projects: Sequence[Project]) -> None:
        """Budget burn for the page, in one grouped query. Only runs when the column is visible.

        The time module is reached through its published service, imported here rather than at
        module scope: nothing outside this branch should drag `time` in, and a module must never
        import another's internals (CLAUDE.md §6).

        A project covered by an active subscription with included hours (#225) burns against
        the sum of those subscriptions' **monthly-equivalent** hours instead of its own stored
        ``budget_hours``, and its period is monthly — one source of truth for "how many hours
        does this project have".
        """
        if not projects:
            return
        from app.modules.time.service import LoggedMinutes, TimeService

        sources = await self._attach_subscription_sources(projects)

        tz = await self._zone()
        effective = {
            p.id: effective_budget(p.budget_hours, p.budget_period, sources.get(p.id, []))
            for p in projects
        }
        periods = {p.id: period_bound(effective[p.id][1], tz=tz) for p in projects}
        logged = await TimeService(self.ctx).minutes_by_project(periods)
        for project in projects:
            minutes = logged.get(project.id, LoggedMinutes())
            budget, period = effective[project.id]
            spent = _hours(minutes.total)
            project.hours = BudgetHours(
                period=period,
                # The local day the period began — what a client sends back as `date_from` to list
                # the entries behind this number (#43). Never the UTC instant's `.date()`.
                period_start=period_start_date(period, tz=tz),
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
        tz = await self._zone()
        grouped: dict[uuid.UUID, list[BudgetedProject]] = {cid: [] for cid in company_ids}
        for row in (await self.ctx.session.execute(stmt)).all():
            grouped[row[1]].append(
                BudgetedProject(
                    id=row[0],
                    company_id=row[1],
                    budget_hours=float(row[2]),
                    budget_period=row[3],
                    period_start=period_bound(row[3], tz=tz),
                )
            )
        return grouped

    async def billable_default(self, project_id: uuid.UUID) -> bool:
        """Does work on this project bill by default? Published so `time` never imports our
        models (§6) — it is the seed for a new entry's ``billable`` (issue #284).

        Deliberately ungated: it seeds a write the caller was already allowed to make, the way
        recording activity does (§16), and it hands back nothing the caller could not infer
        from the entry it just created. An unknown or foreign id answers ``True``, the
        platform-wide default — this is a lookup for a value, not a tenancy check, and the
        org-scoped statement cannot reach another tenant's row either way.
        """
        value = await self.ctx.session.scalar(
            select(Project.billable_default).where(
                Project.org_id == self.ctx.org.id, Project.id == project_id
            )
        )
        return True if value is None else bool(value)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        status: str | None = None,
        q: str | None = None,
        unnamed: bool | None = None,
        mine: bool = False,
        sort: str | None = None,
        hours: bool = False,
        count: bool = True,
        burn: str | None = None,
    ) -> tuple[Sequence[Project], int]:
        # "Over budget" cannot be a SQL condition: the effective budget may come from a
        # covering subscription (#225), and that rule lives in ``_attach_hours`` —
        # re-expressing it as SQL would be a second copy of it (``dashboard_budgets``' own
        # argument). So a burn filter takes the ``dashboard_budgets`` shape instead (#437):
        # fetch the filtered set whole, enrich, filter in Python, cut, and report a total
        # counted over what survived — the SQL COUNT below would count rows the reader never
        # sees, which is the one lie a filtered list must not tell. Any token but ``over`` is
        # ignored (a query string anyone can edit falls back rather than 422s, §9).
        if burn == "over":
            items, _ = await self.list(
                limit=10_000,
                offset=0,
                company_id=company_id,
                status=status,
                q=q,
                unnamed=unnamed,
                mine=mine,
                sort=sort,
                hours=True,
                count=False,
            )
            over = [
                p
                for p in items
                if p.hours.budget_hours  # type: ignore[attr-defined]
                and p.hours.spent_hours >= p.hours.budget_hours  # type: ignore[attr-defined]
            ]
            return over[offset : offset + limit], len(over)

        conditions = []
        if company_id is not None:
            conditions.append(Project.company_id == company_id)
        if status:
            # One status, or several comma-separated — the shape the client list already
            # speaks (#329). "Everything except the archive" is what a project list is normally
            # *for*, and a single-valued filter could not say it: ``status=active`` hides the
            # paused work and the just-delivered work, both of which are still the agency's. A
            # blank between two commas is dropped rather than matched (this arrives from a query
            # string anyone can edit), and a value that names nothing leaves the list unfiltered
            # rather than empty.
            wanted = [s.strip() for s in status.split(",") if s.strip()]
            if wanted:
                conditions.append(Project.status.in_(wanted))
        if q:
            conditions.append(Project.name.ilike(f"%{q.strip()}%"))
        # "The ones nobody named" (#350) — see the twin in ``tasks/service.py``.
        if unnamed is not None:
            conditions.append(Project.unnamed.is_(unnamed))
        if mine:
            # "My projects" matches *any* assignee, not just the primary.
            conditions.append(
                Project.id.in_(self.assignees.entity_ids_for_user(self.ctx.user.id))
            )
        stmt = apply_sort(
            self.repo.scoped_select().where(*conditions),
            sort,
            SORTABLE,
            default=func.lower(Project.name).asc(),
        ).limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # ``count=False`` skips the discarded COUNT(*) for name-only lookups.
        total = (
            int(
                await self.ctx.session.scalar(
                    self.repo.scoped_count_select().where(*conditions)
                )
                or 0
            )
            if count
            else len(items)
        )
        await self._attach_assignees(items)
        await self._attach_company_names(items)
        if hours:
            await self._attach_hours(items)
        return items, total

    async def dashboard_budgets(self, *, limit: int = 4) -> DashboardBudgets:
        """The budgeted active projects burning hottest — what the My Day tile draws (#290).

        The widget used to fetch 200 active projects with the full budget enrichment, every
        assignee row and every custom field, and then keep four. The enrichment is the same
        aggregate either way; the difference is 200 project rows crossing the wire so the
        browser could sort them. Sorted here, cut here, four rows returned.

        Sorting is in Python on purpose: "hottest" is `spent / effective budget`, and the
        effective budget may come from a covering subscription rather than the stored column
        (#225). That rule lives in ``_attach_hours``; re-expressing it as SQL would be a second
        copy of it, which is how the two answers drift.
        """
        stmt = self.repo.scoped_select().where(
            Project.status == ProjectStatus.ACTIVE.value
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        await self._attach_hours(items)
        budgeted = [p for p in items if p.hours.budget_hours]  # type: ignore[attr-defined]
        budgeted.sort(
            key=lambda p: p.hours.spent_hours / p.hours.budget_hours,  # type: ignore[attr-defined]
            reverse=True,
        )
        # After the cut, never before: the tile draws four rows, so the client labels are one
        # query over at most four clients rather than over every active project in the org.
        rows = budgeted[:limit]
        tail = budgeted[limit:]
        await self._attach_company_names(rows)
        return DashboardBudgets(
            items=[
                DashboardBudgetProject(
                    id=p.id,
                    name=p.name,
                    company_name=p.company_name,  # type: ignore[attr-defined]
                    hours=p.hours,  # type: ignore[attr-defined]
                )
                for p in rows
            ],
            # Free: the sort above already has every budgeted active project in hand, so the
            # tile can say "4 van 17" without a second statement (#407).
            total=len(budgeted),
            # Also free, and what lets a donut draw an honest "overig" slice (#437): the
            # tail's hours, not merely its count.
            tail_spent_hours=sum(p.hours.spent_hours for p in tail),  # type: ignore[attr-defined]
            tail_budget_hours=sum(p.hours.budget_hours or 0 for p in tail),  # type: ignore[attr-defined]
            # Over the whole set, so the figure agrees with the ``?burn=over`` list it opens.
            over_budget=sum(
                1
                for p in budgeted
                if p.hours.spent_hours >= p.hours.budget_hours  # type: ignore[attr-defined]
            ),
        )

    async def get(self, project_id: uuid.UUID, *, hours: bool = False) -> Project:
        project = await self.repo.get_or_404(project_id)
        await self._attach_assignees([project])
        await self._attach_company_names([project])
        # Opt-in, exactly as on the list. The detail page asks for it because its budget bar and
        # its Uren panel must both count from the *same* period start (#43) — one the API resolves
        # on the org's own clock (budget.py), which a browser recomputing it in UTC gets wrong twice
        # a year.
        if hours:
            await self._attach_hours([project])
        else:
            # The detail read always says where the budget comes from (#225): the edit form
            # needs the locked state even when the burn aggregate wasn't asked for.
            await self._attach_subscription_sources([project])
        return project

    async def primary_assignee(self, project_id: uuid.UUID) -> uuid.UUID | None:
        """Who owns this project. Published so other modules never import our models (§6)."""
        return await self.assignees.primary(project_id)

    async def create(self, data: ProjectCreate) -> Project:
        self.ctx.require("projects.project.write")
        values = data.model_dump()
        # See the twin in ``tasks/service.py``: nullable on the wire, `NOT NULL` in the column.
        values["unnamed"] = bool(values.get("unnamed"))
        # The description is markdown source (issue #66/#255): strip raw HTML on write.
        values["description"] = sanitize_markdown(values.get("description"))
        values.pop("assignees", None)
        await ensure_parent_in_tenant(
            self.ctx.session, "companies", values.get("company_id"), self.ctx.org.id
        )

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
        # Written on every path that answers with a ``ProjectRead``: a field that is only
        # sometimes populated reads as "this project has no client" on the paths that skip it.
        await self._attach_company_names([project])
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, project.id)
        # Bus-only creation signal for cross-module reactions (the Drive folder, #21/#22).
        # Not in the notifications vocabulary; the roster hears via project.assigned below.
        await emit(
            "project.created",
            self.ctx,
            {"project_id": project.id, "name": project.name, "company_id": project.company_id},
        )
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
        if "description" in values:
            values["description"] = sanitize_markdown(values.get("description"))
        # Naming the thing is what un-marks it (#350) — enforced here, not asked of the caller,
        # so no write path can set a real name and leave the row filed under "nobody named this".
        values.pop("unnamed", None)
        if values.get("name") and project.unnamed:
            values["unnamed"] = False
        # While an active subscription sources the hours (#225), the project's own
        # ``budget_hours`` is not writable — the API guards it, not just the form, so an MCP
        # or script client can't create the drift the UI prevents. Echoing the stored value
        # back is a no-op and passes (clients that PATCH whole objects stay unaffected); the
        # stored value itself stays put as the fallback for when the link is removed.
        sources = await self._attach_subscription_sources([project])
        if "budget_hours" in values and sources.get(project_id):
            current = float(project.budget_hours) if project.budget_hours is not None else None
            if values["budget_hours"] != current:
                raise AppError(
                    "conflict",
                    "errors.projects_budget_hours_locked",
                    status_code=409,
                    fields={"budget_hours": "errors.projects_budget_hours_locked"},
                )
            values.pop("budget_hours")
        if "company_id" in values:
            # A create requires a client (``ProjectCreate``), so an update may move one and
            # never remove it. ``exclude_unset`` is what makes the distinction expressible:
            # the key is absent when the caller said nothing, and present-and-null only when
            # somebody asked to detach. Rows that predate the rule still read ``None`` — they
            # are fixed by picking a client, not by everyone else being allowed to clear one.
            if values["company_id"] is None:
                raise AppError(
                    "validation",
                    "errors.projects_company_required",
                    status_code=422,
                    fields={"company_id": "errors.projects_company_required"},
                )
            await ensure_parent_in_tenant(
                self.ctx.session, "companies", values.get("company_id"), self.ctx.org.id
            )
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
        await self._attach_company_names([project])
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

    # --- org settings (the budget alert) ---------------------------------------- #
    async def settings_row(self) -> ProjectSettings | None:
        """The org's settings row, if it has one. A missing row is not seeded here: writing on
        a read would race two concurrent GETs into a unique-violation, and the absent row
        already means exactly "the defaults" (the leave module's rule)."""
        return await self.ctx.session.scalar(
            self.ctx.repo(ProjectSettings).scoped_select().limit(1)
        )

    async def settings(self) -> ProjectSettingsRead:
        row = await self.settings_row()
        if row is None:
            return ProjectSettingsRead()
        return ProjectSettingsRead(
            budget_alert_emails=row.budget_alert_emails,
            budget_alert_threshold=row.budget_alert_threshold,
        )

    async def update_settings(self, data: ProjectSettingsUpdate) -> ProjectSettingsRead:
        """Write only what the caller sent (absent means leave alone, §18)."""
        self.ctx.require("projects.settings.manage")
        values = {
            key: value
            for key, value in data.model_dump(exclude_unset=True).items()
            if value is not None
        }
        repo = self.ctx.repo(ProjectSettings)
        row = await self.settings_row()
        if row is None:
            await repo.create(**values)
        elif values:
            await repo.update(row, **values)
        return await self.settings()
