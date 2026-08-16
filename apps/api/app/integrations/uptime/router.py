"""REST endpoints for uptime under ``/api/v1/uptime`` (docs/UPTIME.md §13, CLAUDE.md §6, §9).

Deny-by-default: every route declares a permission (§15). The split that matters is that
``instance.manage`` gates the connection and ``monitor.read`` gates the data — an agency can let
everyone see whether a client's site is down without letting anyone repoint schakl at a different
Uptime Kuma.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.entitlements import license_exempt
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.uptime import matching
from app.integrations.uptime.models import InstanceMode
from app.integrations.uptime.schemas import (
    UptimeEnrol,
    UptimeInstanceCreate,
    UptimeInstanceOption,
    UptimeInstanceRead,
    UptimeInstanceUpdate,
    UptimeLinkApplyResult,
    UptimeMonitorCreate,
    UptimeMonitorLink,
    UptimeMonitorRead,
    UptimeMonitorUpdate,
    UptimeProbeResult,
    UptimeProfileCreate,
    UptimeProfileRead,
    UptimeProfileUpdate,
    UptimeReconcile,
    UptimeSyncReport,
)
from app.integrations.uptime.service import (
    UptimeService,
    UptimeWriteService,
    visible_header_names,
)
from app.integrations.uptime.webhook import ingest
from app.schemas import Page

router = APIRouter(prefix="/uptime", tags=["uptime"])


async def _read_instance(
    service: UptimeService, instance, *, counts: tuple[int, int] = (0, 0)
) -> UptimeInstanceRead:
    """Shape one instance for the wire — credentials become *facts about* credentials."""
    return UptimeInstanceRead(
        **{
            field: getattr(instance, field)
            for field in (
                "id",
                "name",
                "mode",
                "base_url",
                "username",
                "ssl_verify",
                "active",
                "status",
                "server_version",
                "last_error",
                "last_checked_at",
                "last_synced_at",
                "created_at",
                "updated_at",
            )
        },
        token_configured=bool(instance.token_encrypted),
        connect_header_names=await visible_header_names(instance),
        insecure=not instance.ssl_verify,
        monitor_count=counts[0],
        group_count=counts[1],
    )


@router.get(
    "/instances",
    response_model=list[UptimeInstanceRead],
    dependencies=[require_permission("uptime.instance.manage")],
)
async def list_instances(
    ctx: RequestContext = Depends(require_context),
) -> list[UptimeInstanceRead]:
    service = UptimeService(ctx)
    instances = await service.list_instances()
    # One grouped query for every count, never one per instance (docs/PERFORMANCE.md).
    counts = await service.monitor_counts([i.id for i in instances])
    return [await _read_instance(service, i, counts=counts.get(i.id, (0, 0))) for i in instances]


@router.post(
    "/instances",
    response_model=UptimeInstanceRead,
    status_code=201,
    dependencies=[require_permission("uptime.instance.manage")],
)
async def create_instance(
    payload: UptimeInstanceCreate, ctx: RequestContext = Depends(require_context)
) -> UptimeInstanceRead:
    service = UptimeService(ctx)
    return await _read_instance(service, await service.create_instance(payload))


@router.get(
    "/instances/selectable",
    response_model=list[UptimeInstanceOption],
    dependencies=[require_permission("uptime.monitor.read")],
)
async def list_selectable_instances(
    ctx: RequestContext = Depends(require_context),
) -> list[UptimeInstanceOption]:
    """Which Uptime Kumas a monitor may be created on — the create form's picker (#366).

    Declared **before** `/instances/{instance_id}` so the literal segment is matched as itself
    rather than as an id, and read on `monitor.read` rather than `instance.manage` for
    `list_profiles`' reason: the form needs to show where a monitor lands, and a gate naming a
    permission the create route does not require is #310's mistake in miniature.

    `writable` is computed here rather than left to the caller so the rule lives in one place: a
    `linked` instance holds no credential, and a monitor created against one can never be pushed.
    """
    instances = await UptimeService(ctx).list_instances()
    return [
        UptimeInstanceOption(
            id=i.id,
            name=i.name,
            mode=i.mode,
            writable=(
                i.active and i.mode == InstanceMode.MANAGED.value and bool(i.token_encrypted)
            ),
        )
        for i in instances
    ]


@router.get(
    "/instances/{instance_id}",
    response_model=UptimeInstanceRead,
    dependencies=[require_permission("uptime.instance.manage")],
)
async def get_instance(
    instance_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> UptimeInstanceRead:
    service = UptimeService(ctx)
    instance = await service.get_instance(instance_id)
    counts = await service.monitor_counts([instance.id])
    return await _read_instance(service, instance, counts=counts.get(instance.id, (0, 0)))


@router.patch(
    "/instances/{instance_id}",
    response_model=UptimeInstanceRead,
    dependencies=[require_permission("uptime.instance.manage")],
)
async def update_instance(
    instance_id: uuid.UUID,
    payload: UptimeInstanceUpdate,
    ctx: RequestContext = Depends(require_context),
) -> UptimeInstanceRead:
    service = UptimeService(ctx)
    return await _read_instance(service, await service.update_instance(instance_id, payload))


@router.delete(
    "/instances/{instance_id}",
    status_code=204,
    dependencies=[require_permission("uptime.instance.manage")],
)
async def delete_instance(
    instance_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await UptimeService(ctx).delete_instance(instance_id)


@router.post(
    "/instances/{instance_id}/enrol",
    response_model=UptimeProbeResult,
    dependencies=[require_permission("uptime.instance.manage")],
)
async def enrol_instance(
    instance_id: uuid.UUID,
    payload: UptimeEnrol,
    ctx: RequestContext = Depends(require_context),
) -> UptimeProbeResult:
    """Authenticate once and store the token. The password never reaches the database.

    Answers `200` with `ok=false` on a refusal rather than raising: the report *is* the answer,
    and an exception would roll back the status update that makes the failure visible on the
    settings screen (`docs/PERFORMANCE.md`'s persist-then-report rule).
    """
    return await UptimeService(ctx).enrol(instance_id, payload)


@router.post(
    "/instances/{instance_id}/probe",
    response_model=UptimeProbeResult,
    dependencies=[require_permission("uptime.instance.manage")],
)
async def probe_instance(
    instance_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> UptimeProbeResult:
    return await UptimeService(ctx).probe(instance_id)


@router.post(
    "/instances/{instance_id}/sync",
    response_model=UptimeSyncReport,
    dependencies=[require_permission("uptime.instance.manage")],
)
async def sync_instance(
    instance_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> UptimeSyncReport:
    """Read every monitor into the mirror. Writes nothing to Uptime Kuma."""
    return await UptimeService(ctx).sync(instance_id)


@router.get(
    "/monitors",
    response_model=Page[UptimeMonitorRead],
    dependencies=[require_permission("uptime.monitor.read")],
)
async def list_monitors(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    instance_id: uuid.UUID | None = Query(None),
    company_id: uuid.UUID | None = Query(None),
    website_id: uuid.UUID | None = Query(None),
    domain_id: uuid.UUID | None = Query(None),
    hosting_id: uuid.UUID | None = Query(None),
    sync_status: str | None = Query(None),
    monitor_type: str | None = Query(
        None, description="Filter by type; 'group' lists the groups an instance has"
    ),
    link_status: str | None = Query(
        None,
        description=(
            "linked / matched / ambiguous / unmatched, 'proposed' for everything a sync found a "
            "candidate for and nobody has confirmed yet, or 'unlinked' for everything still "
            "attachable"
        ),
    ),
    count: bool = Query(True, description="Compute total; set false for pickers"),
    meta: bool = Query(False, description="Resolve display names; skip it for pickers"),
    ctx: RequestContext = Depends(require_context),
) -> Page[UptimeMonitorRead]:
    service = UptimeService(ctx)
    items, total = await service.list_monitors(
        limit=limit,
        offset=offset,
        instance_id=instance_id,
        company_id=company_id,
        website_id=website_id,
        domain_id=domain_id,
        hosting_id=hosting_id,
        sync_status=sync_status,
        monitor_type=monitor_type,
        link_status=link_status,
        count=count,
    )
    # Four extra queries for the whole page, and only when asked. A picker renders names it never
    # reads, so paying for them unconditionally is the shape `docs/PERFORMANCE.md` bans. Each is
    # one statement for the page rather than one per row, which is what
    # `test_the_monitor_list_costs_the_same_however_many_groups_there_are` pins.
    groups = await service.group_names(items) if meta else {}
    children = await service.child_counts(items) if meta else {}
    companies = await service.company_names(items) if meta else {}
    instances = await service.instance_names(items) if meta else {}
    return Page[UptimeMonitorRead](
        items=[_monitor_read(m, groups, children, companies, instances) for m in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/monitors/{monitor_id}",
    response_model=UptimeMonitorRead,
    dependencies=[require_permission("uptime.monitor.read")],
)
async def get_monitor(
    monitor_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> UptimeMonitorRead:
    service = UptimeService(ctx)
    monitor = await service.get_monitor(monitor_id)
    return _monitor_read(monitor, await service.group_names([monitor]))


def _monitor_read(
    monitor,
    groups: dict[uuid.UUID, str] | None = None,
    children: dict[uuid.UUID, int] | None = None,
    companies: dict[uuid.UUID, str] | None = None,
    instances: dict[uuid.UUID, str] | None = None,
) -> UptimeMonitorRead:
    """One monitor for the wire.

    ``remote_active`` is read from the redacted snapshot rather than exposing the snapshot
    itself: it holds a hundred keys of Kuma's internals plus our secret fingerprints, and a
    fingerprint handed to a caller is an oracle.

    ``groups`` and ``children`` are the page's already-resolved lookups, passed in rather than
    fetched here: a name resolved per row is the per-row read a list endpoint must not do.

    ``link_status`` is derived on the way out (`matching.link_status`) rather than stored, so a
    link somebody just made and the status the screen draws can never disagree.
    """
    read = UptimeMonitorRead.model_validate(monitor)
    snapshot = monitor.remote_snapshot or {}
    value = snapshot.get("active")
    read.remote_active = bool(value) if isinstance(value, bool) else None
    read.link_status = matching.link_status(
        website_id=monitor.website_id,
        domain_id=monitor.domain_id,
        hosting_id=monitor.hosting_id,
        candidates=monitor.link_candidates,
    )
    if groups and monitor.parent_id is not None:
        read.parent_name = groups.get(monitor.parent_id)
    if children is not None:
        read.child_count = children.get(monitor.id, 0)
    if companies and monitor.company_id is not None:
        read.company_name = companies.get(monitor.company_id)
    if instances:
        read.instance_name = instances.get(monitor.instance_id)
    return read


# ---------------------------------------------------------------------- gate 2


@router.get(
    "/profiles",
    response_model=list[UptimeProfileRead],
    dependencies=[require_permission("uptime.monitor.read")],
)
async def list_profiles(
    ctx: RequestContext = Depends(require_context),
) -> list[UptimeProfileRead]:
    """Readable on `monitor.read`, writable on `profile.manage`.

    The create form needs to *show* which profile a monitor will follow, and gating the read on
    the manage permission would leave an ordinary member with a picker they cannot populate —
    #310's "mirror the key the call actually makes" applied to a lookup.
    """
    return [
        UptimeProfileRead.model_validate(p) for p in await UptimeWriteService(ctx).list_profiles()
    ]


@router.post(
    "/profiles",
    response_model=UptimeProfileRead,
    status_code=201,
    dependencies=[require_permission("uptime.profile.manage")],
)
async def create_profile(
    payload: UptimeProfileCreate, ctx: RequestContext = Depends(require_context)
) -> UptimeProfileRead:
    return UptimeProfileRead.model_validate(await UptimeWriteService(ctx).create_profile(payload))


@router.patch(
    "/profiles/{profile_id}",
    response_model=UptimeProfileRead,
    dependencies=[require_permission("uptime.profile.manage")],
)
async def update_profile(
    profile_id: uuid.UUID,
    payload: UptimeProfileUpdate,
    ctx: RequestContext = Depends(require_context),
) -> UptimeProfileRead:
    return UptimeProfileRead.model_validate(
        await UptimeWriteService(ctx).update_profile(profile_id, payload)
    )


@router.delete(
    "/profiles/{profile_id}",
    status_code=204,
    dependencies=[require_permission("uptime.profile.manage")],
)
async def delete_profile(
    profile_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await UptimeWriteService(ctx).delete_profile(profile_id)


@router.post(
    "/monitors",
    response_model=UptimeMonitorRead,
    status_code=201,
    dependencies=[require_permission("uptime.monitor.write")],
)
async def create_monitor(
    payload: UptimeMonitorCreate, ctx: RequestContext = Depends(require_context)
) -> UptimeMonitorRead:
    """Create the monitor here and push it to Uptime Kuma."""
    return _monitor_read(await UptimeWriteService(ctx).create_monitor(payload))


@router.patch(
    "/monitors/{monitor_id}",
    response_model=UptimeMonitorRead,
    dependencies=[require_permission("uptime.monitor.write")],
)
async def update_monitor(
    monitor_id: uuid.UUID,
    payload: UptimeMonitorUpdate,
    ctx: RequestContext = Depends(require_context),
) -> UptimeMonitorRead:
    return _monitor_read(await UptimeWriteService(ctx).update_monitor(monitor_id, payload))


@router.post(
    "/monitors/{monitor_id}/link",
    response_model=UptimeMonitorRead,
    dependencies=[require_permission("uptime.monitor.write")],
)
async def link_monitor(
    monitor_id: uuid.UUID,
    payload: UptimeMonitorLink,
    ctx: RequestContext = Depends(require_context),
) -> UptimeMonitorRead:
    """Attach a found monitor to the website, domain or hosting it watches (#321).

    Its own route rather than a `PATCH` field, because it is a different act: it writes nothing
    to Uptime Kuma, it takes one anchor instead of three ids that could contradict each other,
    and it is the one the reconciliation screen posts straight from a candidate it was shown.
    """
    return _monitor_read(await UptimeWriteService(ctx).link_monitor(monitor_id, payload))


@router.post(
    "/instances/{instance_id}/links/apply",
    response_model=UptimeLinkApplyResult,
    dependencies=[require_permission("uptime.monitor.write")],
)
async def apply_links(
    instance_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> UptimeLinkApplyResult:
    """Confirm every unambiguous proposal on this instance; report what was left.

    Declares `monitor.write` and not `instance.manage`: what it writes is monitors. The ambiguous
    ones come back as `skipped` rather than resolved — those are a person's to decide, and doing
    it in bulk would be deciding two hundred of them.
    """
    return await UptimeWriteService(ctx).apply_links(instance_id)


@router.post(
    "/monitors/{monitor_id}/pause",
    response_model=UptimeMonitorRead,
    dependencies=[require_permission("uptime.monitor.pause")],
)
async def pause_monitor(
    monitor_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> UptimeMonitorRead:
    """Its own permission: silencing an alert during a planned migration is an ordinary act,
    and repointing a monitor is not."""
    return _monitor_read(await UptimeWriteService(ctx).set_paused(monitor_id, paused=True))


@router.post(
    "/monitors/{monitor_id}/resume",
    response_model=UptimeMonitorRead,
    dependencies=[require_permission("uptime.monitor.pause")],
)
async def resume_monitor(
    monitor_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> UptimeMonitorRead:
    return _monitor_read(await UptimeWriteService(ctx).set_paused(monitor_id, paused=False))


@router.post(
    "/monitors/{monitor_id}/reconcile",
    response_model=UptimeMonitorRead,
    dependencies=[require_permission("uptime.monitor.write")],
)
async def reconcile_monitor(
    monitor_id: uuid.UUID,
    payload: UptimeReconcile,
    ctx: RequestContext = Depends(require_context),
) -> UptimeMonitorRead:
    """Resolve a drift in the direction the caller names. There is no default direction:
    one overwrites a colleague's edit in Uptime Kuma, the other overwrites schakl's record."""
    return _monitor_read(await UptimeWriteService(ctx).reconcile(monitor_id, payload))


@router.delete(
    "/monitors/{monitor_id}",
    status_code=204,
    dependencies=[require_permission("uptime.monitor.write")],
)
async def delete_monitor(
    monitor_id: uuid.UUID,
    at_kuma: bool = Query(
        False,
        description=(
            "Also delete the monitor in Uptime Kuma. Defaults to false: 'stop tracking this "
            "here' and 'stop watching this client's site' are different decisions."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> None:
    await UptimeWriteService(ctx).delete_monitor(monitor_id, at_kuma=at_kuma)


# ---------------------------------------------------------------------- gate 3


@license_exempt
@router.post(
    "/hook/{token}",
    status_code=200,
    dependencies=[
        no_permission_required(
            "Uptime Kuma posting a heartbeat. The caller holds no session: the token in the "
            "path names the tenant and is compared in constant time, and the body is read for "
            "a monitor id and a state and nothing else (docs/UPTIME.md §11)."
        )
    ],
)
async def uptime_hook(token: str, request: Request) -> Response:
    """Ingest one reported heartbeat.

    Answers a bare status with no body. Every refusal a caller could learn from is a `404` —
    a wrong secret, an unknown instance and an unknown monitor are deliberately identical, or
    the route becomes an oracle for what exists here.

    `license_exempt`: an expired licence makes a module read-only; it does not make a client's
    outage stop having happened. Gate what the agency *does*, never the recording of what has
    already happened to them (docs/PAYMENTS.md's rule, applied to the other place where
    refusing loses information no retry recovers).
    """
    body = await request.body()
    return Response(status_code=await ingest(token, body))
