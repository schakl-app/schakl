"""REST endpoints for uptime under ``/api/v1/uptime`` (docs/UPTIME.md §13, CLAUDE.md §6, §9).

Deny-by-default: every route declares a permission (§15). The split that matters is that
``instance.manage`` gates the connection and ``monitor.read`` gates the data — an agency can let
everyone see whether a client's site is down without letting anyone repoint schakl at a different
Uptime Kuma.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.uptime.schemas import (
    UptimeEnrol,
    UptimeInstanceCreate,
    UptimeInstanceRead,
    UptimeInstanceUpdate,
    UptimeMonitorCreate,
    UptimeMonitorRead,
    UptimeMonitorUpdate,
    UptimeProbeResult,
    UptimeProfileCreate,
    UptimeProfileRead,
    UptimeProfileUpdate,
    UptimeReconcile,
    UptimeSyncReport,
)
from app.modules.uptime.service import (
    UptimeService,
    UptimeWriteService,
    visible_header_names,
)
from app.schemas import Page

router = APIRouter(prefix="/uptime", tags=["uptime"])


async def _read_instance(
    service: UptimeService, instance, *, monitor_count: int = 0
) -> UptimeInstanceRead:
    """Shape one instance for the wire — credentials become *facts about* credentials."""
    return UptimeInstanceRead(
        **{
            field: getattr(instance, field)
            for field in (
                "id", "name", "mode", "base_url", "username", "ssl_verify", "active",
                "status", "server_version", "last_error", "last_checked_at",
                "last_synced_at", "created_at", "updated_at",
            )
        },
        token_configured=bool(instance.token_encrypted),
        connect_header_names=await visible_header_names(instance),
        insecure=not instance.ssl_verify,
        monitor_count=monitor_count,
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
    return [
        await _read_instance(service, i, monitor_count=counts.get(i.id, 0)) for i in instances
    ]


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
    return await _read_instance(service, instance, monitor_count=counts.get(instance.id, 0))


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
    sync_status: str | None = Query(None),
    count: bool = Query(True, description="Compute total; set false for pickers"),
    ctx: RequestContext = Depends(require_context),
) -> Page[UptimeMonitorRead]:
    items, total = await UptimeService(ctx).list_monitors(
        limit=limit,
        offset=offset,
        instance_id=instance_id,
        company_id=company_id,
        website_id=website_id,
        sync_status=sync_status,
        count=count,
    )
    return Page[UptimeMonitorRead](
        items=[_monitor_read(m) for m in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/monitors/{monitor_id}",
    response_model=UptimeMonitorRead,
    dependencies=[require_permission("uptime.monitor.read")],
)
async def get_monitor(
    monitor_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> UptimeMonitorRead:
    return _monitor_read(await UptimeService(ctx).get_monitor(monitor_id))


def _monitor_read(monitor) -> UptimeMonitorRead:
    """One monitor for the wire.

    ``remote_active`` is read from the redacted snapshot rather than exposing the snapshot
    itself: it holds a hundred keys of Kuma's internals plus our secret fingerprints, and a
    fingerprint handed to a caller is an oracle.
    """
    read = UptimeMonitorRead.model_validate(monitor)
    snapshot = monitor.remote_snapshot or {}
    value = snapshot.get("active")
    read.remote_active = bool(value) if isinstance(value, bool) else None
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
