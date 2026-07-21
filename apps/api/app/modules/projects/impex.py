"""CSV import/export shape for projects (issue #77, settings hub round).

Upsert matches on ``name``; the ``company`` column resolves by exact name or UUID. Everything
writes through the module's own service, so an imported project inherits assignees from the
company exactly like one created from the form.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import column, select, table

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.resolvers import name_or_id_resolver
from app.core.tenancy import RequestContext
from app.modules.projects.models import Project, ProjectStatus
from app.modules.projects.schemas import ProjectCreate, ProjectUpdate
from app.modules.projects.service import ProjectService

_companies = table("companies", column("id"), column("name"), column("org_id"))

_FIELDS = (
    "name",
    "description",
    "budget_period",
    "budget_hours",
    "budget_amount",
    "start_date",
    "end_date",
    "company_id",
    "billable_default",
)


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    status = filters.get("status")
    items, _ = await ProjectService(ctx).list(
        limit=limit,
        offset=offset,
        q=filters.get("q"),
        status=ProjectStatus(status) if status else None,
        company_id=filters.get("company_id"),
        mine=bool(filters.get("mine")),
        sort=filters.get("sort"),
        count=False,
    )
    # Resolve company names for the export cells in one grouped query, never one per row.
    company_ids = {p.company_id for p in items if p.company_id}
    names: dict[Any, str] = {}
    if company_ids:
        rows = await ctx.session.execute(
            select(_companies.c.id, _companies.c.name).where(
                _companies.c.org_id == ctx.org.id, _companies.c.id.in_(company_ids)
            )
        )
        names = dict(list(rows))
    for project in items:
        project._impex_company = names.get(project.company_id)  # noqa: SLF001
    return items


async def _find_existing(ctx: RequestContext, values: list[str]) -> dict[str, list[Any]]:
    stmt = ctx.repo(Project).scoped_select().where(Project.name.in_(values))
    found: dict[str, list[Any]] = {}
    for project in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(project.name, []).append(project)
    return found


async def _create(ctx: RequestContext, values: dict[str, Any]) -> None:
    await ProjectService(ctx).create(
        ProjectCreate(
            name=values["name"],
            company_id=values.get("company_id"),
            description=values.get("description"),
            status=ProjectStatus(values["status"])
            if values.get("status")
            else ProjectStatus.ACTIVE,
            billable_default=values.get("billable_default", True) is not False,
            budget_period=values.get("budget_period") or "total",
            budget_hours=values.get("budget_hours"),
            budget_amount=values.get("budget_amount"),
            start_date=values.get("start_date"),
            end_date=values.get("end_date"),
            custom=values.get("custom") or {},
        )
    )


async def _update(ctx: RequestContext, project: Any, values: dict[str, Any]) -> None:
    fields: dict[str, Any] = {key: values[key] for key in _FIELDS if key in values}
    if values.get("status"):
        fields["status"] = ProjectStatus(values["status"])
    if "custom" in values:
        fields["custom"] = values["custom"]
    if fields:
        await ProjectService(ctx).update(project.id, ProjectUpdate(**fields))


PROJECT_IMPEX = ImpexDescriptor(
    entity_type="project",
    read_permission="projects.project.read",
    write_permission="projects.project.write",
    natural_key="name",
    filters=("q", "status", "company_id", "mine", "sort"),
    columns=(
        ImpexColumn("name", required=True),
        ImpexColumn(
            "company",
            data_type="fk",
            field="company_id",
            getter=lambda p: getattr(p, "_impex_company", None),
        ),
        ImpexColumn(
            "status",
            data_type="select",
            clearable=False,
            options=tuple(status.value for status in ProjectStatus),
        ),
        ImpexColumn(
            "budget_period",
            data_type="select",
            clearable=False,
            options=("total", "monthly", "weekly", "daily"),
        ),
        ImpexColumn("budget_hours", data_type="number"),
        ImpexColumn("budget_amount", data_type="number"),
        ImpexColumn("billable_default", data_type="bool", clearable=False),
        ImpexColumn("start_date", data_type="date"),
        ImpexColumn("end_date", data_type="date"),
        ImpexColumn("description"),
    ),
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
    fk_resolvers={"company": name_or_id_resolver("companies")},
)
