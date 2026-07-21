"""Business logic for companies — all DB access via the tenant-scoped repository.

No raw, unscoped queries here (Golden Rule 1 / CLAUDE.md §6). Writes require a non-client role.

Several employees work a client: ``company_assignees`` holds them all, one starred as primary.
``companies.responsible_user_id`` mirrors that primary on every write and is dropped in a later
release (docs/WORKFLOW.md, expand/contract) — read ``primary_assignee()`` instead of the column.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.assignees import AssigneeService
from app.core.auth.models import User
from app.core.customfields import CustomFieldsService
from app.core.events import emit
from app.core.richtext import sanitize_markdown
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.core.urls import reject_dangerous_url
from app.modules.companies.models import Company, CompanyAssignee
from app.modules.companies.schemas import CompanyCreate, CompanyUpdate
from app.schemas import CompanyBudgetHours

ENTITY_TYPE = "company"

# The definition fields whose before/after values the activity trail records (issue #67). Notes
# (freeform) and custom (its own concern) are deliberately left out of the trail's diff.
_AUDITED_FIELDS = (
    "name", "website", "invoice_email", "status", "responsible_user_id",
    # Billing identity (issue #11): what an issued invoice snapshots (#207), so a change
    # here is exactly the kind of definition edit the trail exists to answer for.
    "vat_number", "coc_number", "address_line1", "address_line2",
    "postal_code", "city", "country",
)


def _primary_assignee_name() -> Any:
    """Sort key for "assigned employee": the *primary* assignee's display name.

    A correlated subquery, not a join — a client has many assignees, and joining would multiply
    its row and quietly change the page's contents. Falls back to the email exactly as the UI
    does when someone has no full name, so the list orders the way it reads. A client with nobody
    assigned yields NULL, which ``apply_sort`` files last in both directions.
    """
    return (
        select(func.lower(func.coalesce(User.full_name, User.email)))
        .select_from(CompanyAssignee)
        .join(User, User.id == CompanyAssignee.user_id)
        .where(
            CompanyAssignee.company_id == Company.id,
            CompanyAssignee.org_id == Company.org_id,
            CompanyAssignee.is_primary.is_(True),
        )
        .correlate(Company)
        .scalar_subquery()
    )


# Sortable columns; anything else in ``?sort=`` is rejected (app/core/sorting.py).
# ``name`` sorts case-insensitively: Postgres' default collation would otherwise file every
# lowercase name after every uppercase one, which reads as broken.
SORTABLE = {
    "name": func.lower(Company.name),
    "status": Company.status,
    "assignee": _primary_assignee_name(),
    "created_at": Company.created_at,
    "updated_at": Company.updated_at,
}


def _hours(minutes: int) -> float:
    return round(minutes / 60, 2)


class CompanyService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Company)
        self.custom_fields = CustomFieldsService(ctx)
        self.assignees = AssigneeService(ctx, CompanyAssignee, "company_id")

    async def _attach_assignees(self, companies: Sequence[Company]) -> None:
        """One extra query for the whole page, never one per row (docs/PERFORMANCE.md)."""
        if not companies:
            return
        grouped = await self.assignees.for_entities([c.id for c in companies])
        for company in companies:
            company.assignees = grouped.get(company.id, [])

    async def _attach_hours(self, companies: Sequence[Company]) -> None:
        """Roll each client's budget up from its active, budgeted projects (#25).

        Three grouped queries for the whole page — the client's budgeted projects, the minutes
        those projects burned, and the client's own total — never one per row. Only runs when the
        column is visible, so a list that doesn't show it pays nothing.

        The other modules are reached through their published services, imported inside this
        branch (CLAUDE.md §6): a company knows nothing of a project's or an entry's tables.
        """
        if not companies:
            return
        from app.modules.projects.service import ProjectService
        from app.modules.time.service import LoggedMinutes, TimeService

        company_ids = [c.id for c in companies]
        projects = ProjectService(self.ctx)
        budgeted = await projects.budgeted_active_for_companies(company_ids)

        periods = {p.id: p.period_start for rows in budgeted.values() for p in rows}
        time_service = TimeService(self.ctx)
        by_project = await time_service.minutes_by_project(periods)
        by_company = await time_service.minutes_by_company(company_ids)

        for company in companies:
            rows = budgeted.get(company.id, [])
            company_total = by_company.get(company.id, LoggedMinutes())

            # Everything the budgeted projects ever absorbed — *not* the period figure, or a
            # monthly project's earlier months would masquerade as unbudgeted work.
            absorbed = sum(by_project.get(p.id, LoggedMinutes()).all_time for p in rows)
            unbudgeted = max(0, company_total.all_time - absorbed)

            if not rows:
                # No allowance to report. Show the hours spent anyway — an em-dash where the
                # budget goes, never a fabricated total.
                company.hours = CompanyBudgetHours(
                    period=None,
                    budget_hours=None,
                    remaining_hours=None,
                    unbudgeted_hours=_hours(company_total.all_time),
                    project_count=0,
                )
                continue

            spent = sum(by_project.get(p.id, LoggedMinutes()).total for p in rows)
            billable = sum(by_project.get(p.id, LoggedMinutes()).billable for p in rows)
            unapproved = sum(by_project.get(p.id, LoggedMinutes()).unapproved for p in rows)
            budget = round(sum(p.budget_hours for p in rows), 2)
            periods_seen = {p.budget_period for p in rows}

            company.hours = CompanyBudgetHours(
                # A client whose projects reset on different schedules has no single period the
                # number belongs to; say so rather than pick one.
                period=periods_seen.pop() if len(periods_seen) == 1 else None,
                budget_hours=budget,
                spent_hours=_hours(spent),
                billable_hours=_hours(billable),
                unapproved_hours=_hours(unapproved),
                remaining_hours=round(budget - _hours(spent), 2),
                unbudgeted_hours=_hours(unbudgeted),
                project_count=len(rows),
            )

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        q: str | None = None,
        status: str | None = None,
        mine: bool = False,
        sort: str | None = None,
        hours: bool = False,
        count: bool = True,
    ) -> tuple[Sequence[Company], int]:
        conditions = []
        if q:
            pattern = f"%{q.strip()}%"
            conditions.append(or_(Company.name.ilike(pattern), Company.website.ilike(pattern)))
        if status:
            conditions.append(Company.status == status)
        if mine:
            # "My clients" matches *any* assignee, not just the primary.
            conditions.append(
                Company.id.in_(self.assignees.entity_ids_for_user(self.ctx.user.id))
            )

        stmt = apply_sort(
            self.repo.scoped_select().where(*conditions),
            sort,
            SORTABLE,
            # Unsorted, a search ranks by name and the plain list stays newest-first.
            default=Company.name.asc() if q else Company.created_at.desc(),
        ).limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # ``count=False`` skips the discarded COUNT(*) for name-only lookups (pickers,
        # dashboard grouping) — see docs/PERFORMANCE.md.
        total = (
            int(
                await self.ctx.session.scalar(
                    select(func.count())
                    .select_from(Company)
                    .where(Company.org_id == self.ctx.org.id, *conditions)
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

    async def get(self, company_id: uuid.UUID) -> Company:
        company = await self.repo.get_or_404(company_id)
        await self._attach_assignees([company])
        return company

    async def primary_assignee(self, company_id: uuid.UUID) -> uuid.UUID | None:
        """Who owns this client. Published so other modules never import our models (§6)."""
        return await self.assignees.primary(company_id)

    async def create(self, data: CompanyCreate) -> Company:
        self.ctx.require("companies.company.write")
        values = data.model_dump()
        reject_dangerous_url(values.get("website"), field="website")
        # Notes are markdown source (issue #66/#228): strip raw HTML on write, like every
        # markdown-authored field.
        values["notes"] = sanitize_markdown(values.get("notes"))
        values.pop("assignees", None)
        links = self.assignees.normalize(
            data.assignees, fallback_primary=data.responsible_user_id
        )
        values["responsible_user_id"] = self.assignees.primary_of(links)
        values["status"] = values["status"].value
        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        company = await self.repo.create(**values)
        await self.assignees.replace(company.id, links)
        company.assignees = await self.assignees.for_entity(company.id)
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, company.id)
        # ``company_id``/``status`` are the tasks module's onboarding-template contract; the
        # rest is what notifications needs (issue #16). No ``company.assigned`` here — the
        # roster hears about the client through ``company.created`` and shouldn't be told twice.
        await emit(
            "company.created",
            self.ctx,
            {
                "company_id": company.id,
                "status": company.status,
                "title": company.name,
                "_recipients": [a.user_id for a in company.assignees],
            },
        )
        return company

    async def update(self, company_id: uuid.UUID, data: CompanyUpdate) -> Company:
        self.ctx.require("companies.company.write")
        company = await self.repo.get_or_404(company_id)
        previous_status = company.status
        before_fields = snapshot(company, _AUDITED_FIELDS)
        values = data.model_dump(exclude_unset=True)
        if "website" in values:
            reject_dangerous_url(values.get("website"), field="website")
        if "notes" in values:
            values["notes"] = sanitize_markdown(values.get("notes"))
        # ``replace`` is delete-then-insert, so who is *new* has to be read before the write.
        roster_touched = "assignees" in values or "responsible_user_id" in values
        before: set[uuid.UUID] = (
            {a.user_id for a in await self.assignees.for_entity(company_id)}
            if roster_touched
            else set()
        )
        if values.get("status") is not None:
            values["status"] = values["status"].value
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

        company = await self.repo.update(company, **values)
        if links is not None:
            await self.assignees.replace(company.id, links)
        elif "responsible_user_id" in values:
            await self.assignees.set_primary(company.id, values["responsible_user_id"])
        company.assignees = await self.assignees.for_entity(company.id)
        after = {a.user_id for a in company.assignees}
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, company.id, before_fields, snapshot(company, _AUDITED_FIELDS)
        )

        if company.status != previous_status:
            await emit(
                "company.status_changed",
                self.ctx,
                {
                    "company_id": company.id,
                    "status": company.status,
                    "previous_status": previous_status,
                    # ``from``/``to`` are the vocabulary every status event on the bus speaks,
                    # so one notification renderer serves tasks, projects and companies.
                    "from": previous_status,
                    "to": company.status,
                    "title": company.name,
                    "_recipients": sorted(after),
                },
            )
        # Only a request that touched the roster can add anyone; ``before`` is deliberately
        # empty otherwise, so the diff would otherwise re-announce the whole roster.
        if roster_touched and (added := after - before):
            await emit(
                "company.assigned",
                self.ctx,
                {"company_id": company.id, "title": company.name, "_recipients": sorted(added)},
            )
        return company

    async def delete(self, company_id: uuid.UUID) -> None:
        self.ctx.require("companies.company.delete")
        company = await self.repo.get_or_404(company_id)
        await self.repo.delete(company)
