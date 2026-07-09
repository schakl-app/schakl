"""REST endpoints for employee leave under ``/api/v1/leave`` (CLAUDE.md §6, §9, §14).

Requests + approval flow, computed balances, tenant-configurable leave types, contract
hours, yearly entitlements, and the team absence feed the calendar/timesheet render.
Reads/writes default to the current user; managers may pass ``user_id`` / ``all_users``.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.tenancy import RequestContext, require_context
from app.modules.leave.models import LeaveRequestStatus
from app.modules.leave.schemas import (
    EntitlementGenerate,
    GenerateResult,
    LeaveBalance,
    LeaveEntitlementRead,
    LeaveEntitlementUpsert,
    LeaveProfileRead,
    LeaveProfileUpdate,
    LeaveRequestCreate,
    LeaveRequestDecision,
    LeaveRequestRead,
    LeaveRequestUpdate,
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
@router.get("/types", response_model=list[LeaveTypeRead])
async def list_types(
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[LeaveTypeRead]:
    items = await LeaveService(ctx).list_types(include_inactive=include_inactive)
    return [LeaveTypeRead.model_validate(t) for t in items]


@router.post("/types", response_model=LeaveTypeRead, status_code=201)
async def create_type(
    payload: LeaveTypeCreate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveTypeRead:
    return LeaveTypeRead.model_validate(await LeaveService(ctx).create_type(payload))


@router.patch("/types/{type_id}", response_model=LeaveTypeRead)
async def update_type(
    type_id: uuid.UUID,
    payload: LeaveTypeUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveTypeRead:
    return LeaveTypeRead.model_validate(await LeaveService(ctx).update_type(type_id, payload))


@router.delete("/types/{type_id}", status_code=204)
async def delete_type(
    type_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await LeaveService(ctx).delete_type(type_id)


# --- profiles (contract hours) ------------------------------------------------ #
@router.get("/profile", response_model=LeaveProfileRead)
async def my_profile(ctx: RequestContext = Depends(require_context)) -> LeaveProfileRead:
    hours = await LeaveService(ctx).hours_per_week(ctx.user.id)
    return LeaveProfileRead(user_id=ctx.user.id, hours_per_week=hours)


@router.get("/profiles", response_model=list[LeaveProfileRead])
async def list_profiles(
    ctx: RequestContext = Depends(require_context),
) -> list[LeaveProfileRead]:
    """Contract hours per user (managers). Users without a row default to 40."""
    profiles = await LeaveService(ctx).list_profiles()
    return [
        LeaveProfileRead(user_id=user_id, hours_per_week=hours)
        for user_id, hours in profiles.items()
    ]


@router.put("/profiles/{user_id}", response_model=LeaveProfileRead)
async def set_profile(
    user_id: uuid.UUID,
    payload: LeaveProfileUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveProfileRead:
    profile = await LeaveService(ctx).set_profile(user_id, payload.hours_per_week)
    return LeaveProfileRead(user_id=profile.user_id, hours_per_week=profile.hours_per_week)


# --- entitlements --------------------------------------------------------------- #
@router.get("/entitlements", response_model=list[LeaveEntitlementRead])
async def list_entitlements(
    year: int = Query(..., ge=2000, le=2100),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[LeaveEntitlementRead]:
    """Own entitlements; managers see everyone's when no ``user_id`` is given."""
    items = await LeaveService(ctx).list_entitlements(year=year, user_id=user_id)
    return [LeaveEntitlementRead.model_validate(e) for e in items]


@router.put("/entitlements", response_model=LeaveEntitlementRead)
async def upsert_entitlement(
    payload: LeaveEntitlementUpsert,
    ctx: RequestContext = Depends(require_context),
) -> LeaveEntitlementRead:
    return LeaveEntitlementRead.model_validate(
        await LeaveService(ctx).upsert_entitlement(payload)
    )


@router.post("/entitlements/generate", response_model=GenerateResult)
async def generate_entitlements(
    payload: EntitlementGenerate,
    ctx: RequestContext = Depends(require_context),
) -> GenerateResult:
    created = await LeaveService(ctx).generate_entitlements(payload.year)
    return GenerateResult(created=created)


# --- balances --------------------------------------------------------------------- #
@router.get("/balance", response_model=list[LeaveBalance])
async def balances(
    year: int = Query(..., ge=2000, le=2100),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[LeaveBalance]:
    return await LeaveService(ctx).balances(year=year, user_id=user_id)


# --- dashboard widget --------------------------------------------------------------- #
@router.get("/summary", response_model=LeaveSummary)
async def summary(ctx: RequestContext = Depends(require_context)) -> LeaveSummary:
    return await LeaveService(ctx).summary()


# --- team absence feed (calendar / timesheet overlay) -------------------------------- #
@router.get("/team", response_model=list[TeamLeaveItem])
async def team(
    date_from: date = Query(...),
    date_to: date = Query(...),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[TeamLeaveItem]:
    return await LeaveService(ctx).team(date_from=date_from, date_to=date_to, user_id=user_id)


# --- requests -------------------------------------------------------------------------- #
@router.get("/requests", response_model=Page[LeaveRequestRead])
async def list_requests(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: uuid.UUID | None = Query(None),
    all_users: bool = Query(False),
    year: int | None = Query(None, ge=2000, le=2100),
    status: LeaveRequestStatus | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> Page[LeaveRequestRead]:
    items, total = await LeaveService(ctx).list(
        limit=limit, offset=offset, user_id=user_id, all_users=all_users, year=year, status=status
    )
    return Page(
        items=[LeaveRequestRead.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/requests", response_model=LeaveRequestRead, status_code=201)
async def create_request(
    payload: LeaveRequestCreate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    return LeaveRequestRead.model_validate(await LeaveService(ctx).create(payload))


@router.get("/requests/{request_id}", response_model=LeaveRequestRead)
async def get_request(
    request_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    return LeaveRequestRead.model_validate(await LeaveService(ctx).get(request_id))


@router.patch("/requests/{request_id}", response_model=LeaveRequestRead)
async def update_request(
    request_id: uuid.UUID,
    payload: LeaveRequestUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    return LeaveRequestRead.model_validate(await LeaveService(ctx).update(request_id, payload))


@router.post("/requests/{request_id}/decide", response_model=LeaveRequestRead)
async def decide_request(
    request_id: uuid.UUID,
    payload: LeaveRequestDecision,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    """Approve or reject a pending request (managers)."""
    return LeaveRequestRead.model_validate(
        await LeaveService(ctx).decide(request_id, approved=payload.approved, note=payload.note)
    )


@router.post("/requests/{request_id}/cancel", response_model=LeaveRequestRead)
async def cancel_request(
    request_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> LeaveRequestRead:
    return LeaveRequestRead.model_validate(await LeaveService(ctx).cancel(request_id))
