"""REST endpoints for employee leave under ``/api/v1/leave`` (CLAUDE.md §6, §9, §14).

Requests + approval flow, computed balances, tenant-configurable leave types, contract
hours, yearly entitlements, and the team absence feed the calendar/timesheet render.
Reads/writes default to the current user; managers may pass ``user_id`` / ``all_users``.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.leave.models import LeaveRequestStatus
from app.modules.leave.schedule import WorkSchedule, average_day_hours
from app.modules.leave.schedule import parse as parse_schedule
from app.modules.leave.schemas import (
    EntitlementGenerate,
    GenerateResult,
    HolidayImport,
    HolidayImportResult,
    LeaveBalance,
    LeaveEntitlementRead,
    LeaveEntitlementUpsert,
    LeaveHolidayCreate,
    LeaveHolidayRead,
    LeaveHolidayUpdate,
    LeavePreviewResult,
    LeaveProfileRead,
    LeaveProfileSummary,
    LeaveProfileUpdate,
    LeaveRateRead,
    LeaveRateUpdate,
    LeaveRequestCreate,
    LeaveRequestDecision,
    LeaveRequestPreview,
    LeaveRequestRead,
    LeaveRequestUpdate,
    LeaveSettingsRead,
    LeaveSettingsUpdate,
    LeaveSummary,
    LeaveTypeCreate,
    LeaveTypeRead,
    LeaveTypeUpdate,
    TeamLeaveItem,
)
from app.modules.leave.service import LeaveService
from app.schemas import Page

router = APIRouter(prefix="/leave", tags=["leave"])


# --- leave types (tenant config) --------------------------------------------- #
@router.get(
    "/types",
    response_model=list[LeaveTypeRead],
    dependencies=[require_permission("leave.request.read")],
)
async def list_types(
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[LeaveTypeRead]:
    items = await LeaveService(ctx).list_types(include_inactive=include_inactive)
    return [LeaveTypeRead.model_validate(t) for t in items]


@router.post(
    "/types",
    response_model=LeaveTypeRead,
    status_code=201,
    dependencies=[require_permission("leave.type.write")],
)
async def create_type(
    payload: LeaveTypeCreate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveTypeRead:
    return LeaveTypeRead.model_validate(await LeaveService(ctx).create_type(payload))


@router.patch(
    "/types/{type_id}",
    response_model=LeaveTypeRead,
    dependencies=[require_permission("leave.type.write")],
)
async def update_type(
    type_id: uuid.UUID,
    payload: LeaveTypeUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveTypeRead:
    return LeaveTypeRead.model_validate(await LeaveService(ctx).update_type(type_id, payload))


@router.delete(
    "/types/{type_id}",
    status_code=204,
    dependencies=[require_permission("leave.type.write")],
)
async def delete_type(
    type_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await LeaveService(ctx).delete_type(type_id)


# --- org settings (default work schedule) -------------------------------------- #
@router.get(
    "/settings",
    response_model=LeaveSettingsRead,
    dependencies=[require_permission("leave.profile.manage")],
)
async def get_settings(ctx: RequestContext = Depends(require_context)) -> LeaveSettingsRead:
    """The schedule a new employee inherits. An org that never saved one gets the default."""
    return LeaveSettingsRead(default_schedule=await LeaveService(ctx).default_schedule())


@router.put(
    "/settings",
    response_model=LeaveSettingsRead,
    dependencies=[require_permission("leave.profile.manage")],
)
async def update_settings(
    payload: LeaveSettingsUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveSettingsRead:
    row = await LeaveService(ctx).update_settings(payload)
    return LeaveSettingsRead(
        default_schedule=WorkSchedule.model_validate(row.default_schedule),
        holiday_country=row.holiday_country,
        holiday_auto_import=row.holiday_auto_import,
    )


# --- holidays (#47) ------------------------------------------------------------ #
@router.get(
    "/holidays",
    response_model=list[LeaveHolidayRead],
    dependencies=[require_permission("leave.request.read")],
)
async def list_holidays(
    year: int | None = Query(None, ge=2000, le=2100),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[LeaveHolidayRead]:
    """The org's calendar. Any staff member: it drives the agenda and the timesheet.

    By ``year`` (Settings) or by ``date_from``/``date_to`` — a timesheet week straddles New
    Year's Eve, and a range spares it a second call. The range wins when both are given.
    """
    items = await LeaveService(ctx).list_holidays(
        year=year, date_from=date_from, date_to=date_to, include_inactive=include_inactive
    )
    return [LeaveHolidayRead.model_validate(h) for h in items]


@router.post(
    "/holidays",
    response_model=LeaveHolidayRead,
    status_code=201,
    dependencies=[require_permission("leave.holiday.write")],
)
async def create_holiday(
    payload: LeaveHolidayCreate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveHolidayRead:
    return LeaveHolidayRead.model_validate(await LeaveService(ctx).create_holiday(payload))


@router.patch(
    "/holidays/{holiday_id}",
    response_model=LeaveHolidayRead,
    dependencies=[require_permission("leave.holiday.write")],
)
async def update_holiday(
    holiday_id: uuid.UUID,
    payload: LeaveHolidayUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveHolidayRead:
    return LeaveHolidayRead.model_validate(
        await LeaveService(ctx).update_holiday(holiday_id, payload)
    )


@router.delete(
    "/holidays/{holiday_id}",
    status_code=204,
    dependencies=[require_permission("leave.holiday.write")],
)
async def delete_holiday(
    holiday_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await LeaveService(ctx).delete_holiday(holiday_id)


@router.post(
    "/holidays/import",
    response_model=HolidayImportResult,
    dependencies=[require_permission("leave.holiday.write")],
)
async def import_holidays(
    payload: HolidayImport,
    ctx: RequestContext = Depends(require_context),
) -> HolidayImportResult:
    """Re-run the generator for a year. Manual and deactivated rows survive untouched."""
    return await LeaveService(ctx).import_holidays(payload)


# --- profiles (work schedule + contract hours) --------------------------------- #
@router.get(
    "/profile",
    response_model=LeaveProfileRead,
    dependencies=[require_permission("leave.request.read")],
)
async def my_profile(ctx: RequestContext = Depends(require_context)) -> LeaveProfileRead:
    """The caller's **effective** schedule — merged server-side, on purpose (#46)."""
    schedule, hours, inherited = await LeaveService(ctx).profile_for(ctx.user.id)
    return LeaveProfileRead(
        user_id=ctx.user.id,
        hours_per_week=hours,
        hours_per_day=average_day_hours(schedule),
        schedule=schedule,
        inherited=inherited,
    )


@router.get(
    "/profiles",
    response_model=list[LeaveProfileSummary],
    dependencies=[require_permission("leave.profile.manage")],
)
async def list_profiles(
    ctx: RequestContext = Depends(require_context),
) -> list[LeaveProfileSummary]:
    """Contract hours + own schedule per user (managers). A null schedule follows the org's."""
    service = LeaveService(ctx)
    default = await service.default_schedule()
    return [
        LeaveProfileSummary(
            user_id=user_id,
            hours_per_week=hours,
            hours_per_day=average_day_hours(schedule or default),
            schedule=schedule,
        )
        for user_id, hours, schedule in await service.list_profiles()
    ]


@router.put(
    "/profiles/{user_id}",
    response_model=LeaveProfileSummary,
    dependencies=[require_permission("leave.profile.manage")],
)
async def set_profile(
    user_id: uuid.UUID,
    payload: LeaveProfileUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveProfileSummary:
    service = LeaveService(ctx)
    profile = await service.set_profile(user_id, payload)
    own = parse_schedule(profile.schedule)
    return LeaveProfileSummary(
        user_id=profile.user_id,
        hours_per_week=profile.hours_per_week,
        hours_per_day=average_day_hours(own or await service.default_schedule()),
        schedule=own,
    )


# --- hourly rate (#82) --------------------------------------------------------- #
@router.get(
    "/rate",
    response_model=LeaveRateRead,
    dependencies=[require_permission("leave.rate.read")],
)
async def my_rate(ctx: RequestContext = Depends(require_context)) -> LeaveRateRead:
    """The caller's own hourly rate (salary-adjacent — a member sees only their own)."""
    user_id, rate = await LeaveService(ctx).get_rate()
    return LeaveRateRead(user_id=user_id, hourly_rate=rate)


@router.get(
    "/rates",
    response_model=list[LeaveRateRead],
    dependencies=[require_permission("leave.rate.read")],
)
async def list_rates(ctx: RequestContext = Depends(require_context)) -> list[LeaveRateRead]:
    """Every employee's rate for the managers' roster. Requires ``leave.rate.read:any``."""
    return [
        LeaveRateRead(user_id=user_id, hourly_rate=rate)
        for user_id, rate in await LeaveService(ctx).list_rates()
    ]


@router.get(
    "/rate/{user_id}",
    response_model=LeaveRateRead,
    dependencies=[require_permission("leave.rate.read")],
)
async def user_rate(
    user_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRateRead:
    """One employee's rate. Own on ``:own``; anyone's on ``:any``."""
    uid, rate = await LeaveService(ctx).get_rate(user_id)
    return LeaveRateRead(user_id=uid, hourly_rate=rate)


@router.put(
    "/rate/{user_id}",
    response_model=LeaveRateRead,
    dependencies=[require_permission("leave.rate.write")],
)
async def set_rate(
    user_id: uuid.UUID,
    payload: LeaveRateUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRateRead:
    """Set or clear an employee's rate (admin). Attributed nowhere — the rate is current state."""
    profile = await LeaveService(ctx).set_rate(user_id, payload.hourly_rate)
    return LeaveRateRead(user_id=profile.user_id, hourly_rate=profile.hourly_rate)


# --- entitlements --------------------------------------------------------------- #
@router.get(
    "/entitlements",
    response_model=list[LeaveEntitlementRead],
    dependencies=[require_permission("leave.entitlement.read")],
)
async def list_entitlements(
    year: int = Query(..., ge=2000, le=2100),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[LeaveEntitlementRead]:
    """Own entitlements; managers see everyone's when no ``user_id`` is given."""
    items = await LeaveService(ctx).list_entitlements(year=year, user_id=user_id)
    return [LeaveEntitlementRead.model_validate(e) for e in items]


@router.put(
    "/entitlements",
    response_model=LeaveEntitlementRead,
    dependencies=[require_permission("leave.entitlement.write")],
)
async def upsert_entitlement(
    payload: LeaveEntitlementUpsert,
    ctx: RequestContext = Depends(require_context),
) -> LeaveEntitlementRead:
    return LeaveEntitlementRead.model_validate(
        await LeaveService(ctx).upsert_entitlement(payload)
    )


@router.post(
    "/entitlements/generate",
    response_model=GenerateResult,
    dependencies=[require_permission("leave.entitlement.write")],
)
async def generate_entitlements(
    payload: EntitlementGenerate,
    ctx: RequestContext = Depends(require_context),
) -> GenerateResult:
    created = await LeaveService(ctx).generate_entitlements(payload.year)
    return GenerateResult(created=created)


# --- balances --------------------------------------------------------------------- #
@router.get(
    "/balance",
    response_model=list[LeaveBalance],
    dependencies=[require_permission("leave.request.read")],
)
async def balances(
    year: int = Query(..., ge=2000, le=2100),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[LeaveBalance]:
    return await LeaveService(ctx).balances(year=year, user_id=user_id)


# --- dashboard widget --------------------------------------------------------------- #
@router.get(
    "/summary",
    response_model=LeaveSummary,
    dependencies=[require_permission("leave.request.read")],
)
async def summary(ctx: RequestContext = Depends(require_context)) -> LeaveSummary:
    return await LeaveService(ctx).summary()


# --- team absence feed (calendar / timesheet overlay) -------------------------------- #
@router.get(
    "/team",
    response_model=list[TeamLeaveItem],
    dependencies=[require_permission("leave.request.read")],
)
async def team(
    date_from: date = Query(...),
    date_to: date = Query(...),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[TeamLeaveItem]:
    return await LeaveService(ctx).team(date_from=date_from, date_to=date_to, user_id=user_id)


# --- requests -------------------------------------------------------------------------- #
@router.get(
    "/requests",
    response_model=Page[LeaveRequestRead],
    dependencies=[require_permission("leave.request.read")],
)
async def list_requests(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: uuid.UUID | None = Query(None),
    all_users: bool = Query(False),
    year: int | None = Query(None, ge=2000, le=2100),
    status: LeaveRequestStatus | None = Query(None),
    sort: str | None = Query(
        None, description="employee | start_date | end_date | hours | status, '-' desc"
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[LeaveRequestRead]:
    items, total = await LeaveService(ctx).list(
        limit=limit,
        offset=offset,
        user_id=user_id,
        all_users=all_users,
        year=year,
        status=status,
        sort=sort,
    )
    return Page(
        items=[LeaveRequestRead.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/requests",
    response_model=LeaveRequestRead,
    status_code=201,
    dependencies=[require_permission("leave.request.write")],
)
async def create_request(
    payload: LeaveRequestCreate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    return LeaveRequestRead.model_validate(await LeaveService(ctx).create(payload))


@router.post(
    "/requests/preview",
    response_model=LeavePreviewResult,
    dependencies=[require_permission("leave.request.read")],
)
async def preview_request(
    payload: LeaveRequestPreview,
    ctx: RequestContext = Depends(require_context),
) -> LeavePreviewResult:
    """What a span costs, before it is submitted — so the number shown is the number stored.

    Declared **above** ``/requests/{request_id}``: FastAPI matches in declaration order, and
    ``preview`` would otherwise be parsed as a request id and 422 on the UUID.
    """
    return await LeaveService(ctx).preview(payload)


@router.get(
    "/requests/{request_id}",
    response_model=LeaveRequestRead,
    dependencies=[require_permission("leave.request.read")],
)
async def get_request(
    request_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    return LeaveRequestRead.model_validate(await LeaveService(ctx).get(request_id))


@router.patch(
    "/requests/{request_id}",
    response_model=LeaveRequestRead,
    dependencies=[require_permission("leave.request.write")],
)
async def update_request(
    request_id: uuid.UUID,
    payload: LeaveRequestUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    return LeaveRequestRead.model_validate(await LeaveService(ctx).update(request_id, payload))


@router.post(
    "/requests/{request_id}/decide",
    response_model=LeaveRequestRead,
    dependencies=[require_permission("leave.request.approve")],
)
async def decide_request(
    request_id: uuid.UUID,
    payload: LeaveRequestDecision,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    """Approve or reject a pending request (managers)."""
    return LeaveRequestRead.model_validate(
        await LeaveService(ctx).decide(request_id, approved=payload.approved, note=payload.note)
    )


@router.post(
    "/requests/{request_id}/cancel",
    response_model=LeaveRequestRead,
    dependencies=[require_permission("leave.request.write")],
)
async def cancel_request(
    request_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    return LeaveRequestRead.model_validate(await LeaveService(ctx).cancel(request_id))
