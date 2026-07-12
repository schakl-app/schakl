"""CSV import/export shape for time entries — the timesheet (issue #77, settings hub round).

**Create-only import** (``natural_key=None``): a time entry has no natural key a spreadsheet
could carry. Times are the wall clock the user typed, stored as UTC (§8), so the CSV shape is
``date`` + ``start``/``end`` (HH:MM) — exactly what the timesheet shows. Import goes through
``TimeEntryService.create``, which means **rows are created as the importer's own entries**
(the service is the authority on ownership); the exported ``user`` column is readonly so a
round-trip still accepts the file. Approval/invoice flags are derived state, also readonly.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import column, select, table

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.resolvers import name_or_id_resolver
from app.core.tenancy import RequestContext
from app.modules.time.schemas import TimeEntryCreate
from app.modules.time.service import TimeService

_companies = table("companies", column("id"), column("name"), column("org_id"))
_projects = table("projects", column("id"), column("name"), column("org_id"))
_users = table("users", column("id"), column("email"))


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    """The module's own list. A holder of ``time.entry.read:any`` exports the whole org
    (unless they ask for ``mine``); anyone else exports their own timesheet — the same rule
    as the report screen, resolved here instead of a 403 so the hub's plain Export works
    for members too."""
    org_wide = not filters.get("mine", False) and ctx.can("time.entry.read", "any")
    items, _ = await TimeService(ctx).list(
        limit=limit,
        offset=offset,
        user_id=filters.get("user_id"),
        company_id=filters.get("company_id"),
        project_id=filters.get("project_id"),
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
        running=False,
        all_users=org_wide,
        sort=filters.get("sort"),
    )

    async def names(ref, ids):
        if not ids:
            return {}
        label = ref.c.name if "name" in ref.c else ref.c.email
        stmt = select(ref.c.id, label).where(ref.c.id.in_(ids))
        if "org_id" in ref.c:
            stmt = stmt.where(ref.c.org_id == ctx.org.id)
        return dict(list(await ctx.session.execute(stmt)))

    companies = await names(_companies, {e.company_id for e in items if e.company_id})
    projects = await names(_projects, {e.project_id for e in items if e.project_id})
    users = await names(_users, {e.user_id for e in items if e.user_id})
    for entry in items:
        entry._impex_company = companies.get(entry.company_id)  # noqa: SLF001
        entry._impex_project = projects.get(entry.project_id)  # noqa: SLF001
        entry._impex_user = users.get(entry.user_id)  # noqa: SLF001
    return items


async def _find_existing(ctx: RequestContext, values: list[str]) -> dict[str, list[Any]]:
    return {}  # create-only: never matched


async def _create(ctx: RequestContext, values: dict[str, Any]) -> None:
    started_at = datetime.fromisoformat(f"{values['date']}T{values['start']}:00").replace(
        tzinfo=UTC
    )
    ended_at = (
        datetime.fromisoformat(f"{values['date']}T{values['end']}:00").replace(tzinfo=UTC)
        if values.get("end")
        else None
    )
    await TimeService(ctx).create(
        TimeEntryCreate(
            started_at=started_at,
            ended_at=ended_at,
            minutes=int(float(values["minutes"]))
            if ended_at is None and values.get("minutes")
            else None,
            break_minutes=int(float(values.get("break_minutes") or 0)),
            description=values.get("description"),
            billable=values.get("billable", True) is not False,
            company_id=values.get("company_id"),
            project_id=values.get("project_id"),
        )
    )


async def _update(ctx: RequestContext, entry: Any, values: dict[str, Any]) -> None:
    raise NotImplementedError  # unreachable: create-only (natural_key=None)


TIME_ENTRY_IMPEX = ImpexDescriptor(
    entity_type="time_entry",
    read_permission="time.entry.read",
    write_permission="time.entry.write",
    natural_key=None,
    filters=("mine", "user_id", "company_id", "project_id", "date_from", "date_to", "sort"),
    columns=(
        ImpexColumn(
            "date",
            data_type="date",
            required=True,
            getter=lambda e: e.started_at.date() if e.started_at else None,
        ),
        ImpexColumn(
            "start",
            data_type="time",
            required=True,
            getter=lambda e: e.started_at.strftime("%H:%M") if e.started_at else None,
        ),
        ImpexColumn(
            "end",
            data_type="time",
            getter=lambda e: e.ended_at.strftime("%H:%M") if e.ended_at else None,
        ),
        # Derived by the service from start/end (or drives the end when no end is given).
        ImpexColumn("minutes", data_type="number"),
        ImpexColumn("break_minutes", data_type="number"),
        ImpexColumn(
            "company",
            data_type="fk",
            field="company_id",
            getter=lambda e: getattr(e, "_impex_company", None),
        ),
        ImpexColumn(
            "project",
            data_type="fk",
            field="project_id",
            getter=lambda e: getattr(e, "_impex_project", None),
        ),
        ImpexColumn("description"),
        ImpexColumn("billable", data_type="bool", clearable=False),
        # Ownership and sign-off state are the service's, never a CSV's (readonly).
        ImpexColumn(
            "user", readonly=True, getter=lambda e: getattr(e, "_impex_user", None)
        ),
        ImpexColumn(
            "approved", readonly=True, getter=lambda e: e.approved_at is not None
        ),
        ImpexColumn(
            "invoiced", readonly=True, getter=lambda e: e.invoiced_at is not None
        ),
    ),
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
    fk_resolvers={
        "company": name_or_id_resolver("companies"),
        "project": name_or_id_resolver("projects"),
    },
)
