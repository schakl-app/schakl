"""Business logic for employee leave (CLAUDE.md §6, §14).

Members manage their **own** requests; managers (owner/admin) decide them and may act on
another user's behalf. All reads/writes go through the tenant-scoped repository (Golden
Rule 1). Balances are computed, never stored: entitled − approved − pending, per type/year
(the year of ``start_date``). Over-requests on balance-tracked types are blocked with a
clear error, as are overlapping requests.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.core.auth.models import User
from app.core.events import emit
from app.core.models import Membership
from app.core.roles import Role
from app.core.sorting import apply_sort, user_sort_name
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.leave.models import (
    LeaveEntitlement,
    LeaveProfile,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
)
from app.modules.leave.schemas import (
    LeaveBalance,
    LeaveEntitlementUpsert,
    LeaveRequestCreate,
    LeaveRequestUpdate,
    LeaveSummary,
    LeaveTypeCreate,
    LeaveTypeUpdate,
    TeamLeaveItem,
)

_DEFAULT_HOURS_PER_WEEK = Decimal("40")

# Seeded per org (also in the create-tables migration for existing orgs). Tenant-editable
# config, not law: the Dutch defaults are 4 statutory weeks expiring 6 months into the next
# year, extra-statutory hours keeping 5 years, and sick leave reported without approval.
DEFAULT_LEAVE_TYPES: list[dict] = [
    {
        "key": "vacation_statutory",
        "label_i18n": {"nl": "Wettelijke vakantie", "en": "Statutory vacation"},
        "color": "emerald",
        "paid": True,
        "tracks_balance": True,
        "requires_approval": True,
        "default_weeks": Decimal("4"),
        "carry_over_months": 6,
        "position": 10,
    },
    {
        "key": "vacation_extra",
        "label_i18n": {"nl": "Bovenwettelijke vakantie", "en": "Extra vacation"},
        "color": "teal",
        "paid": True,
        "tracks_balance": True,
        "requires_approval": True,
        "default_weeks": Decimal("1"),
        "carry_over_months": 60,
        "position": 20,
    },
    {
        "key": "sick",
        "label_i18n": {"nl": "Ziek", "en": "Sick"},
        "color": "orange",
        "paid": True,
        "tracks_balance": False,
        "requires_approval": False,
        "default_weeks": None,
        "carry_over_months": None,
        "position": 30,
    },
    {
        "key": "special",
        "label_i18n": {"nl": "Bijzonder verlof", "en": "Special leave"},
        "color": "violet",
        "paid": True,
        "tracks_balance": False,
        "requires_approval": True,
        "default_weeks": None,
        "carry_over_months": None,
        "position": 40,
    },
    {
        "key": "unpaid",
        "label_i18n": {"nl": "Onbetaald verlof", "en": "Unpaid leave"},
        "color": "sky",
        "paid": False,
        "tracks_balance": False,
        "requires_approval": True,
        "default_weeks": None,
        "carry_over_months": None,
        "position": 50,
    },
]

_OCCUPYING = (LeaveRequestStatus.PENDING.value, LeaveRequestStatus.APPROVED.value)


def _now() -> datetime:
    return datetime.now(UTC)


# Columns a client may sort by; anything else in ``?sort=`` is rejected (app/core/sorting.py).
# ``employee`` orders by the requester's display name, never by their user id — a list sorted by
# a person must order the way it reads. ``type`` cannot be a sort key: a leave type's label is
# per-locale tenant data in a JSONB column, so ``key`` is the closest honest thing.
SORTABLE = {
    "employee": user_sort_name(LeaveRequest.user_id),
    "start_date": LeaveRequest.start_date,
    "end_date": LeaveRequest.end_date,
    "hours": LeaveRequest.hours,
    "status": LeaveRequest.status,
    "created_at": LeaveRequest.created_at,
}


class LeaveService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.requests = ctx.repo(LeaveRequest)
        self.types = ctx.repo(LeaveType)
        self.profiles = ctx.repo(LeaveProfile)
        self.entitlements = ctx.repo(LeaveEntitlement)

    # --- access scoping ------------------------------------------------------ #
    def _effective_user_id(self, user_id: uuid.UUID | None) -> uuid.UUID:
        """Resolve whose leave to act on: self, or another user if a manager asks."""
        if user_id is None or user_id == self.ctx.user.id:
            return self.ctx.user.id
        if not self.ctx.role.can_manage:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        return user_id

    async def _owned_or_404(self, request_id: uuid.UUID) -> LeaveRequest:
        request = await self.requests.get_or_404(request_id)
        if request.user_id != self.ctx.user.id and not self.ctx.role.can_manage:
            # Don't reveal another user's request exists.
            raise AppError("not_found", "errors.not_found", status_code=404)
        return request

    # --- leave types ---------------------------------------------------------- #
    async def list_types(self, *, include_inactive: bool = False) -> Sequence[LeaveType]:
        await self._ensure_default_types()
        stmt = self.types.scoped_select().order_by(LeaveType.position.asc(), LeaveType.key)
        if not include_inactive:
            stmt = stmt.where(LeaveType.active.is_(True))
        return (await self.ctx.session.execute(stmt)).scalars().all()

    async def _ensure_default_types(self) -> None:
        """Seed the Dutch default types once per org (idempotent; skipped for read-only roles)."""
        if not self.ctx.can_write:
            return
        if await self.types.count() > 0:
            return
        for spec in DEFAULT_LEAVE_TYPES:
            await self.types.create(**spec)

    async def create_type(self, data: LeaveTypeCreate) -> LeaveType:
        self.ctx.ensure_can_manage()
        existing = await self.ctx.session.scalar(
            self.types.scoped_select().where(LeaveType.key == data.key)
        )
        if existing is not None:
            raise AppError("conflict", "errors.conflict", status_code=409)
        return await self.types.create(**data.model_dump())

    async def update_type(self, type_id: uuid.UUID, data: LeaveTypeUpdate) -> LeaveType:
        self.ctx.ensure_can_manage()
        leave_type = await self.types.get_or_404(type_id)
        return await self.types.update(leave_type, **data.model_dump(exclude_unset=True))

    async def delete_type(self, type_id: uuid.UUID) -> None:
        """Hard-delete only unused types; ones with history should be deactivated instead."""
        self.ctx.ensure_can_manage()
        leave_type = await self.types.get_or_404(type_id)
        if await self.requests.count(leave_type_id=type_id) > 0:
            raise AppError("conflict", "errors.leave_type_in_use", status_code=409)
        # Entitlements follow via the FK's ON DELETE CASCADE.
        await self.types.delete(leave_type)

    # --- profiles (contract hours) --------------------------------------------- #
    async def hours_per_week(self, user_id: uuid.UUID) -> Decimal:
        profile = await self.ctx.session.scalar(
            self.profiles.scoped_select().where(LeaveProfile.user_id == user_id)
        )
        return profile.hours_per_week if profile else _DEFAULT_HOURS_PER_WEEK

    async def list_profiles(self) -> dict[uuid.UUID, Decimal]:
        """Contract hours per user for every org member (managers)."""
        self.ctx.ensure_can_manage()
        rows = (await self.ctx.session.execute(self.profiles.scoped_select())).scalars().all()
        return {p.user_id: p.hours_per_week for p in rows}

    async def set_profile(self, user_id: uuid.UUID, hours: Decimal) -> LeaveProfile:
        self.ctx.ensure_can_manage()
        await self._member_or_404(user_id)
        profile = await self.ctx.session.scalar(
            self.profiles.scoped_select().where(LeaveProfile.user_id == user_id)
        )
        if profile is None:
            return await self.profiles.create(user_id=user_id, hours_per_week=hours)
        return await self.profiles.update(profile, hours_per_week=hours)

    async def _member_or_404(self, user_id: uuid.UUID) -> None:
        membership = await self.ctx.session.scalar(
            select(Membership).where(
                Membership.org_id == self.ctx.org.id, Membership.user_id == user_id
            )
        )
        if membership is None:
            raise AppError("not_found", "errors.not_found", status_code=404)

    # --- entitlements ------------------------------------------------------------ #
    async def list_entitlements(
        self, *, year: int, user_id: uuid.UUID | None = None
    ) -> Sequence[LeaveEntitlement]:
        uid = self._effective_user_id(user_id)
        stmt = self.entitlements.scoped_select().where(LeaveEntitlement.year == year)
        if not (self.ctx.role.can_manage and user_id is None):
            stmt = stmt.where(LeaveEntitlement.user_id == uid)
        return (await self.ctx.session.execute(stmt)).scalars().all()

    async def upsert_entitlement(self, data: LeaveEntitlementUpsert) -> LeaveEntitlement:
        self.ctx.ensure_can_manage()
        await self.types.get_or_404(data.leave_type_id)
        await self._member_or_404(data.user_id)
        existing = await self.ctx.session.scalar(
            self.entitlements.scoped_select().where(
                LeaveEntitlement.user_id == data.user_id,
                LeaveEntitlement.leave_type_id == data.leave_type_id,
                LeaveEntitlement.year == data.year,
            )
        )
        if existing is None:
            return await self.entitlements.create(**data.model_dump())
        return await self.entitlements.update(existing, hours=data.hours, note=data.note)

    async def generate_entitlements(self, year: int) -> int:
        """Create missing entitlements: default_weeks × contract hours, per staff member."""
        self.ctx.ensure_can_manage()
        types = [
            t
            for t in await self.list_types()
            if t.tracks_balance and t.default_weeks is not None
        ]
        staff = (
            (
                await self.ctx.session.execute(
                    select(Membership.user_id).where(
                        Membership.org_id == self.ctx.org.id,
                        Membership.role != Role.CLIENT.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        existing = {
            (e.user_id, e.leave_type_id)
            for e in await self.list_entitlements(year=year)
        }
        created = 0
        for user_id in staff:
            hours_week = await self.hours_per_week(user_id)
            for leave_type in types:
                if (user_id, leave_type.id) in existing:
                    continue
                await self.entitlements.create(
                    user_id=user_id,
                    leave_type_id=leave_type.id,
                    year=year,
                    hours=(leave_type.default_weeks or 0) * hours_week,
                )
                created += 1
        return created

    # --- balances ------------------------------------------------------------------ #
    async def _request_hours_by_type(
        self, user_id: uuid.UUID, year: int, status: str
    ) -> dict[uuid.UUID, Decimal]:
        stmt = (
            select(LeaveRequest.leave_type_id, func.coalesce(func.sum(LeaveRequest.hours), 0))
            .where(
                LeaveRequest.org_id == self.ctx.org.id,
                LeaveRequest.user_id == user_id,
                LeaveRequest.status == status,
                LeaveRequest.start_date >= date(year, 1, 1),
                LeaveRequest.start_date < date(year + 1, 1, 1),
            )
            .group_by(LeaveRequest.leave_type_id)
        )
        return {row[0]: Decimal(row[1]) for row in (await self.ctx.session.execute(stmt)).all()}

    async def balances(self, *, year: int, user_id: uuid.UUID | None = None) -> list[LeaveBalance]:
        uid = self._effective_user_id(user_id)
        types = [t for t in await self.list_types() if t.tracks_balance]
        entitled: dict[uuid.UUID, Decimal] = {}
        for ent in await self.list_entitlements(year=year, user_id=uid):
            entitled[ent.leave_type_id] = entitled.get(ent.leave_type_id, Decimal(0)) + ent.hours
        approved = await self._request_hours_by_type(uid, year, LeaveRequestStatus.APPROVED.value)
        pending = await self._request_hours_by_type(uid, year, LeaveRequestStatus.PENDING.value)
        result: list[LeaveBalance] = []
        for t in types:
            ent = entitled.get(t.id, Decimal(0))
            appr = approved.get(t.id, Decimal(0))
            pend = pending.get(t.id, Decimal(0))
            result.append(
                LeaveBalance(
                    leave_type_id=t.id,
                    year=year,
                    entitled_hours=ent,
                    approved_hours=appr,
                    pending_hours=pend,
                    remaining_hours=ent - appr - pend,
                )
            )
        return result

    # --- requests ------------------------------------------------------------------- #
    async def _validate_request(
        self,
        *,
        user_id: uuid.UUID,
        leave_type: LeaveType,
        start_date: date,
        end_date: date,
        hours: Decimal,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        if end_date < start_date:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"end_date": "errors.leave_end_before_start"},
            )
        # No two occupying (pending/approved) requests may overlap for the same user.
        stmt = self.requests.scoped_select().where(
            LeaveRequest.user_id == user_id,
            LeaveRequest.status.in_(_OCCUPYING),
            LeaveRequest.start_date <= end_date,
            LeaveRequest.end_date >= start_date,
        )
        if exclude_id is not None:
            stmt = stmt.where(LeaveRequest.id != exclude_id)
        if (await self.ctx.session.execute(stmt.limit(1))).scalars().first() is not None:
            raise AppError("conflict", "errors.leave_overlap", status_code=409)
        # Block over-requests on balance-tracked types (CLAUDE.md §14).
        if leave_type.tracks_balance:
            year = start_date.year
            balances = {
                b.leave_type_id: b
                for b in await self.balances(year=year, user_id=user_id)
            }
            balance = balances.get(leave_type.id)
            remaining = balance.remaining_hours if balance else Decimal(0)
            if exclude_id is not None:
                # Editing: the request's own current hours are still counted in the balance.
                current = await self.requests.get_or_404(exclude_id)
                if current.leave_type_id == leave_type.id and current.start_date.year == year:
                    remaining += Decimal(current.hours)
            if hours > remaining:
                raise AppError(
                    "insufficient_balance", "errors.leave_insufficient_balance", status_code=400
                )

    async def create(self, data: LeaveRequestCreate) -> LeaveRequest:
        self.ctx.ensure_can_write()
        uid = self._effective_user_id(data.user_id)
        leave_type = await self.types.get_or_404(data.leave_type_id)
        if not leave_type.active:
            raise AppError("validation", "errors.validation", status_code=422)
        await self._validate_request(
            user_id=uid,
            leave_type=leave_type,
            start_date=data.start_date,
            end_date=data.end_date,
            hours=data.hours,
        )
        # Types without approval (sick) are registrations: they go straight to approved.
        status = (
            LeaveRequestStatus.PENDING.value
            if leave_type.requires_approval
            else LeaveRequestStatus.APPROVED.value
        )
        request = await self.requests.create(
            user_id=uid,
            leave_type_id=data.leave_type_id,
            start_date=data.start_date,
            end_date=data.end_date,
            hours=data.hours,
            note=data.note,
            status=status,
        )
        # Only a request that actually waits on somebody is worth interrupting them for; a
        # registration (sick leave) is already decided (issue #16).
        if request.status == LeaveRequestStatus.PENDING.value:
            await self._emit_leave("leave.requested", request, await self._managers())
        return request

    async def _managers(self) -> list[uuid.UUID]:
        """Who may approve leave here — owners and admins (``Role.can_manage``)."""
        return list(
            (
                await self.ctx.session.execute(
                    select(Membership.user_id).where(
                        Membership.org_id == self.ctx.org.id,
                        Membership.role.in_(
                            [role.value for role in Role if role.can_manage]
                        ),
                    )
                )
            ).scalars()
        )

    async def _emit_leave(
        self, event: str, request: LeaveRequest, recipients: Sequence[uuid.UUID]
    ) -> None:
        """Announce a leave decision on the bus (CLAUDE.md §6 — no cross-module imports)."""
        await emit(
            event,
            self.ctx,
            {
                "leave_request_id": request.id,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "hours": request.hours,
                "_recipients": list(recipients),
            },
        )

    async def update(self, request_id: uuid.UUID, data: LeaveRequestUpdate) -> LeaveRequest:
        self.ctx.ensure_can_write()
        request = await self._owned_or_404(request_id)
        # Members may only reshape their own *pending* request; managers may also fix
        # approved ones (mirrors the approved-lock rule on time entries).
        if request.status != LeaveRequestStatus.PENDING.value and not self.ctx.role.can_manage:
            raise AppError("approved_locked", "errors.approved_locked", status_code=403)
        if request.status not in _OCCUPYING:
            raise AppError("conflict", "errors.leave_decided", status_code=409)
        values = data.model_dump(exclude_unset=True)
        leave_type = await self.types.get_or_404(
            values.get("leave_type_id", request.leave_type_id)
        )
        await self._validate_request(
            user_id=request.user_id,
            leave_type=leave_type,
            start_date=values.get("start_date", request.start_date),
            end_date=values.get("end_date", request.end_date),
            hours=Decimal(values.get("hours", request.hours)),
            exclude_id=request.id,
        )
        return await self.requests.update(request, **values)

    async def decide(
        self, request_id: uuid.UUID, *, approved: bool, note: str | None = None
    ) -> LeaveRequest:
        self.ctx.ensure_can_manage()
        request = await self.requests.get_or_404(request_id)
        if request.status != LeaveRequestStatus.PENDING.value:
            raise AppError("conflict", "errors.leave_decided", status_code=409)
        request = await self.requests.update(
            request,
            status=(
                LeaveRequestStatus.APPROVED.value
                if approved
                else LeaveRequestStatus.REJECTED.value
            ),
            decided_by_user_id=self.ctx.user.id,
            decided_at=_now(),
            decision_note=note,
        )
        # The person who asked is the person who needs the answer.
        await self._emit_leave(
            "leave.approved" if approved else "leave.rejected", request, [request.user_id]
        )
        return request

    async def cancel(self, request_id: uuid.UUID) -> LeaveRequest:
        """Requester may cancel while pending; approved leave needs a manager."""
        self.ctx.ensure_can_write()
        request = await self._owned_or_404(request_id)
        if request.status == LeaveRequestStatus.APPROVED.value:
            if not self.ctx.role.can_manage:
                raise AppError("approved_locked", "errors.approved_locked", status_code=403)
        elif request.status != LeaveRequestStatus.PENDING.value:
            raise AppError("conflict", "errors.leave_decided", status_code=409)
        return await self.requests.update(
            request, status=LeaveRequestStatus.CANCELLED.value
        )

    async def get(self, request_id: uuid.UUID) -> LeaveRequest:
        return await self._owned_or_404(request_id)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        user_id: uuid.UUID | None = None,
        all_users: bool = False,
        year: int | None = None,
        status: LeaveRequestStatus | None = None,
        sort: str | None = None,
    ) -> tuple[Sequence[LeaveRequest], int]:
        conditions = []
        if all_users:
            self.ctx.ensure_can_manage()
        else:
            conditions.append(LeaveRequest.user_id == self._effective_user_id(user_id))
        if year is not None:
            conditions.append(LeaveRequest.start_date >= date(year, 1, 1))
            conditions.append(LeaveRequest.start_date < date(year + 1, 1, 1))
        if status is not None:
            conditions.append(LeaveRequest.status == status.value)
        stmt = apply_sort(
            self.requests.scoped_select().where(*conditions),
            sort,
            SORTABLE,
            default=LeaveRequest.start_date.desc(),
        ).limit(limit).offset(offset)
        items = (await self.ctx.session.execute(stmt)).scalars().all()
        count_stmt = (
            select(func.count())
            .select_from(LeaveRequest)
            .where(LeaveRequest.org_id == self.ctx.org.id, *conditions)
        )
        total = int(await self.ctx.session.scalar(count_stmt) or 0)
        return items, total

    # --- team calendar feed ------------------------------------------------------------ #
    async def team(
        self, *, date_from: date, date_to: date, user_id: uuid.UUID | None = None
    ) -> list[TeamLeaveItem]:
        """Occupying absences overlapping the range, with names — the calendar/timesheet feed.

        Open to all staff (who's off is normal team-visible information in an agency), but
        not to external ``client`` users.
        """
        if self.ctx.role == Role.CLIENT:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        stmt = (
            select(LeaveRequest, User)
            .join(User, User.id == LeaveRequest.user_id)
            .where(
                LeaveRequest.org_id == self.ctx.org.id,
                LeaveRequest.status.in_(_OCCUPYING),
                LeaveRequest.start_date <= date_to,
                LeaveRequest.end_date >= date_from,
            )
            .order_by(LeaveRequest.start_date.asc())
        )
        if user_id is not None:
            stmt = stmt.where(LeaveRequest.user_id == user_id)
        rows = (await self.ctx.session.execute(stmt)).all()
        return [
            TeamLeaveItem(
                id=req.id,
                user_id=req.user_id,
                user_name=user.full_name or user.email,
                leave_type_id=req.leave_type_id,
                start_date=req.start_date,
                end_date=req.end_date,
                hours=req.hours,
                status=LeaveRequestStatus(req.status),
            )
            for req, user in rows
        ]

    # --- dashboard widget ---------------------------------------------------------------- #
    async def summary(self) -> LeaveSummary:
        today = _now().date()
        year = today.year
        balances = await self.balances(year=year)
        remaining = sum((b.remaining_hours for b in balances), Decimal(0))
        pending = await self.ctx.session.scalar(
            select(func.count())
            .select_from(LeaveRequest)
            .where(
                LeaveRequest.org_id == self.ctx.org.id,
                LeaveRequest.user_id == self.ctx.user.id,
                LeaveRequest.status == LeaveRequestStatus.PENDING.value,
            )
        )
        next_leave = (
            await self.ctx.session.execute(
                self.requests.scoped_select()
                .where(
                    LeaveRequest.user_id == self.ctx.user.id,
                    LeaveRequest.status == LeaveRequestStatus.APPROVED.value,
                    LeaveRequest.end_date >= today,
                )
                .order_by(LeaveRequest.start_date.asc())
                .limit(1)
            )
        ).scalars().first()
        return LeaveSummary(
            year=year,
            remaining_hours=remaining,
            hours_per_week=await self.hours_per_week(self.ctx.user.id),
            pending_count=int(pending or 0),
            next_leave_start=next_leave.start_date if next_leave else None,
            next_leave_end=next_leave.end_date if next_leave else None,
        )
