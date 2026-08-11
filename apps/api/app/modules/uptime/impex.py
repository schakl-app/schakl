"""The uptime module's spreadsheet shape (CLAUDE.md §17, docs/UPTIME.md).

**Export-only, and the reason is not squeamishness.** Every imported monitor row would be an
outbound Socket.IO round-trip — connect, authenticate, create, re-read — and the import path is
synchronous (`MAX_IMPORT_ROWS` is what keeps it honest until issue #77's background job lands).
A 200-row file would hold one request open for minutes and spend the instance's login budget
doing it, which is the shape §3 exists to prevent. `importable=False` says so, exactly as
`leave` does for a different reason.

What an agency actually wants from a spreadsheet here is the *register*: which client sites are
watched, by which instance, and which are not. That is a read, and it is the half that is
useful today.

The **contributed** columns are the other half. `websites` owns the entity an agency lists, so
uptime contributes its monitor count and status to *that* export rather than teaching websites
about monitors (§6, the panels pattern applied to impex). Keys are namespaced by the
contributor, and a contributed column may never be required.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.impex.resolvers import name_or_id_resolver
from app.core.impex.spec import ImpexColumn, ImpexDescriptor, ImpexExtension
from app.core.tenancy import RequestContext
from app.modules.uptime.models import UptimeMonitor


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[UptimeMonitor]:
    from app.modules.uptime.service import UptimeService

    items, _ = await UptimeService(ctx).list_monitors(
        limit=limit,
        offset=offset,
        company_id=filters.get("company_id"),
        # `count=False`: an export streams pages and never renders a total, so paying for one
        # per page is the shape docs/PERFORMANCE.md bans.
        count=False,
    )
    return items


async def _update_row(ctx: RequestContext, row: UptimeMonitor, values: dict[str, Any]) -> None:
    """Write the link fields through the ordinary service path.

    Reached only from the bulk route: this descriptor mounts no import. It goes through
    `update_monitor` so a selection edit is the form's write path repeated — activity trail and
    all — rather than a second writer that means something subtly different.
    """
    from app.modules.uptime.schemas import UptimeMonitorUpdate
    from app.modules.uptime.service import UptimeWriteService

    payload = UptimeMonitorUpdate(
        **{k: v for k, v in values.items() if k in {"company_id", "website_id"}}
    )
    await UptimeWriteService(ctx).update_monitor(row.id, payload)


def _unsupported(*_args: Any, **_kwargs: Any) -> Any:
    """`importable=False` means core mounts no import route, so nothing can reach these."""
    raise NotImplementedError("uptime monitors are export-only (docs/UPTIME.md §17)")


UPTIME_MONITOR_IMPEX = ImpexDescriptor(
    entity_type="uptime_monitor",
    read_permission="uptime.monitor.read",
    write_permission="uptime.monitor.write",
    columns=(
        ImpexColumn(key="name", label_key="uptime.field.name"),
        ImpexColumn(key="monitor_type", label_key="uptime.field.monitor_type"),
        ImpexColumn(key="target", label_key="uptime.field.target"),
        ImpexColumn(key="interval_seconds", label_key="uptime.field.interval", data_type="number"),
        ImpexColumn(key="sync_status", label_key="uptime.field.sync_status"),
        # The distinction the whole module is built on, in one column: what we decided versus
        # what Uptime Kuma last said. A register that hid drift would be worth less than no
        # register.
        ImpexColumn(key="drift", label_key="uptime.field.drift", getter=lambda m: (
            ", ".join(m.drift_fields or []) or ""
        )),
        ImpexColumn(key="kuma_monitor_id", label_key="uptime.field.kuma_id", data_type="number"),
        # Whose monitor this is. A register that does not say which client a watched host
        # belongs to is half a register — and it is the one column a *selection* may set, which
        # is why it is a real `fk` rather than a derived getter (§18: an editable key must name
        # a column the import shape actually carries).
        ImpexColumn(
            key="company",
            field="company_id",
            data_type="fk",
            clearable=True,
            label_key="uptime.field.company",
        ),
    ),
    natural_keys=(),
    # Only the shared filter params core actually mounts (`impex/router.FILTER_PARAMS`).
    # `instance_id` and `sync_status` are this module's own and have no place in that shared
    # vocabulary; the export carries `sync_status` as a *column* instead, which is what a
    # register is read for anyway.
    filters=("q", "company_id"),
    fetch_page=_fetch_page,
    find_existing=_unsupported,
    create_row=_unsupported,
    # Not `_unsupported`: a **bulk** edit borrows this shape's writer (§18), and it is the one
    # write that does not push — attaching a freshly-adopted instance's monitors to their
    # clients touches nothing at Uptime Kuma.
    update_row=_update_row,
    fk_resolvers={"company": name_or_id_resolver("companies")},
    importable=False,
)


async def _hydrate_websites(ctx: RequestContext, rows: Sequence[Any]) -> None:
    """Load every row's monitors in **one** query.

    Without this the contributed columns go N+1 — the failure §17 names outright, and the one a
    contributed column is most likely to introduce because its getter looks free.
    """
    from sqlalchemy import func

    ids = [r.id for r in rows if getattr(r, "id", None)]
    if not ids:
        return
    repo = ctx.repo(UptimeMonitor)
    stmt = (
        repo.scoped_select()
        .with_only_columns(
            UptimeMonitor.website_id,
            func.count(UptimeMonitor.id),
            func.count(UptimeMonitor.id).filter(UptimeMonitor.sync_status == "drift"),
        )
        .where(UptimeMonitor.website_id.in_(ids))
        .group_by(UptimeMonitor.website_id)
    )
    folded = {row[0]: (row[1], row[2]) for row in (await ctx.session.execute(stmt)).all()}
    for row in rows:
        total, drifted = folded.get(row.id, (0, 0))
        row.__dict__["_uptime_count"] = total
        row.__dict__["_uptime_drift"] = drifted


async def _apply_nothing(_ctx: RequestContext, _host: Any, _values: dict[str, Any]) -> None:
    """These columns are read-only, so an import writes nothing through them.

    ``apply`` is required by the contract rather than optional, which is the right default: a
    contributed column that *looks* writable and silently is not would be worse than one that
    says so. Both columns are marked ``readonly`` so core never offers them on an import.
    """
    return None


UPTIME_WEBSITE_COLUMNS = ImpexExtension(
    entity_type="website",
    module="uptime",
    # No write permission: nothing here is writable, so there is no capability to demand. A
    # caller who cannot read monitors simply never sees the columns (§17's caller-dependent
    # catalog) rather than hitting a mid-import 403.
    write_permissions=(),
    apply=_apply_nothing,
    columns=(
        ImpexColumn(
            key="uptime.monitors",
            label_key="uptime.field.monitor_count",
            data_type="number",
            readonly=True,
            getter=lambda w: w.__dict__.get("_uptime_count", 0),
        ),
        ImpexColumn(
            key="uptime.drifted",
            label_key="uptime.field.drift",
            data_type="number",
            readonly=True,
            getter=lambda w: w.__dict__.get("_uptime_drift", 0),
        ),
    ),
    hydrate=_hydrate_websites,
)
