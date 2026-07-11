"""Business logic for employee leave (CLAUDE.md §6, §14).

Members manage their **own** requests; managers (owner/admin) decide them and may act on
another user's behalf. All reads/writes go through the tenant-scoped repository (Golden
Rule 1). Balances are computed, never stored: entitled − approved − pending, per type/year
(the year of ``start_date``). Over-requests on balance-tracked types **warn but submit**
(#109) — the balance just reads negative and the approver decides; overlapping requests
stay a hard error.
"""

from __future__ import annotations

import uuid
from calendar import monthrange
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from app.core.auth.models import User
from app.core.events import emit
from app.core.models import Membership
from app.core.permissions.service import permission_holder_ids
from app.core.sorting import apply_sort, user_sort_name
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.leave import holidays
from app.modules.leave import schedule as sched
from app.modules.leave.models import (
    EmploymentContract,
    LeaveEntitlement,
    LeaveHoliday,
    LeaveProfile,
    LeaveRecurringDay,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveSettings,
    LeaveType,
)
from app.modules.leave.schemas import (
    EmploymentContractCreate,
    EmploymentContractUpdate,
    HolidayImport,
    HolidayImportResult,
    LeaveBalance,
    LeaveDayHours,
    LeaveEntitlementUpsert,
    LeaveHolidayCreate,
    LeaveHolidayUpdate,
    LeavePreviewResult,
    LeaveProfileUpdate,
    LeaveRecurringDayCreate,
    LeaveRecurringDayUpdate,
    LeaveRequestCreate,
    LeaveRequestPreview,
    LeaveRequestUpdate,
    LeaveSettingsUpdate,
    LeaveSummary,
    LeaveTypeCreate,
    LeaveTypeUpdate,
    TeamLeaveItem,
)

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
        # Roostervrije tijd / ADV (#65): a movable free day drawn from the gap between the
        # scheduled and the contract week. Auto-approve (the employee moves it themselves),
        # balance-tracked, no carry-over by default (tenant-editable — never hardcoded law, §14).
        "key": "roostervrij",
        "label_i18n": {"nl": "Roostervrije tijd (ADV)", "en": "Rostered days off (ADV)"},
        "color": "cyan",
        "paid": True,
        "tracks_balance": True,
        "requires_approval": False,
        "accrues_schedule_gap": True,
        "default_weeks": None,
        "carry_over_months": 0,
        "position": 25,
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

#: "Not looked up yet", distinct from "looked up, and there is no row".
_UNSET = object()

#: Midnight-to-midnight, in minutes: the window a day with no requested time covers.
_DAY = sched.MINUTES_PER_DAY


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
        self.settings = ctx.repo(LeaveSettings)
        self.holidays = ctx.repo(LeaveHoliday)
        self.contracts = ctx.repo(EmploymentContract)
        self.recurring = ctx.repo(LeaveRecurringDay)
        # One settings read per request, not one per profile resolved (docs/PERFORMANCE.md).
        self._settings_row: LeaveSettings | None | object = _UNSET
        # Memoized per request: update() consults it for the bounce *and* the backdate gate.
        self._self_approval: bool | None = None

    # --- access scoping ------------------------------------------------------ #
    def _effective_user_id(self, user_id: uuid.UUID | None) -> uuid.UUID:
        """Whose leave to act on: your own, or another's with ``leave.request.read:any``."""
        if user_id is None or user_id == self.ctx.user.id:
            return self.ctx.user.id
        self.ctx.require("leave.request.read", scope="any")
        return user_id

    async def _owned_or_404(self, request_id: uuid.UUID) -> LeaveRequest:
        """404, not 403, on someone else's request — a 403 would confirm that it exists."""
        request = await self.requests.get_or_404(request_id)
        if request.user_id != self.ctx.user.id and not self.ctx.can(
            "leave.request.read", scope="any"
        ):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return request

    def _ensure_writable(self, request: LeaveRequest) -> None:
        """``leave.request.write:own`` covers your own request; someone else's needs ``:any``."""
        scope = None if request.user_id == self.ctx.user.id else "any"
        self.ctx.require("leave.request.write", scope=scope)

    # --- leave types ---------------------------------------------------------- #
    async def list_types(self, *, include_inactive: bool = False) -> Sequence[LeaveType]:
        await self._ensure_default_types()
        stmt = self.types.scoped_select().order_by(LeaveType.position.asc(), LeaveType.key)
        if not include_inactive:
            stmt = stmt.where(LeaveType.active.is_(True))
        return (await self.ctx.session.execute(stmt)).scalars().all()

    async def _ensure_default_types(self) -> None:
        """Seed the Dutch default types once per org (idempotent; skipped for read-only roles)."""
        if not self.ctx.can("leave.request.write"):
            return
        if await self.types.count() > 0:
            return
        for spec in DEFAULT_LEAVE_TYPES:
            await self.types.create(**spec)

    async def create_type(self, data: LeaveTypeCreate) -> LeaveType:
        self.ctx.require("leave.type.write")
        existing = await self.ctx.session.scalar(
            self.types.scoped_select().where(LeaveType.key == data.key)
        )
        if existing is not None:
            raise AppError("conflict", "errors.conflict", status_code=409)
        return await self.types.create(**data.model_dump())

    async def update_type(self, type_id: uuid.UUID, data: LeaveTypeUpdate) -> LeaveType:
        self.ctx.require("leave.type.write")
        leave_type = await self.types.get_or_404(type_id)
        return await self.types.update(leave_type, **data.model_dump(exclude_unset=True))

    async def delete_type(self, type_id: uuid.UUID) -> None:
        """Hard-delete only unused types; ones with history should be deactivated instead."""
        self.ctx.require("leave.type.write")
        leave_type = await self.types.get_or_404(type_id)
        if await self.requests.count(leave_type_id=type_id) > 0:
            raise AppError("conflict", "errors.leave_type_in_use", status_code=409)
        # Entitlements follow via the FK's ON DELETE CASCADE.
        await self.types.delete(leave_type)

    # --- org settings (the default work schedule) ------------------------------- #
    async def settings_row(self) -> LeaveSettings | None:
        """The org's settings row, if it has one. Memoized: several readers per request.

        A missing row is not seeded here. Writing on a read would race two concurrent GETs into
        a unique-violation, and the absent row already means exactly "the defaults".
        """
        if self._settings_row is _UNSET:
            self._settings_row = await self.ctx.session.scalar(
                self.settings.scoped_select().limit(1)
            )
        return self._settings_row

    async def default_schedule(self) -> sched.WorkSchedule:
        row = await self.settings_row()
        return sched.parse(row.default_schedule) if row else sched.default_schedule()

    async def update_settings(self, data: LeaveSettingsUpdate) -> LeaveSettings:
        """Write only what the caller sent. See ``LeaveSettingsUpdate`` — two screens save here."""
        self.ctx.require("leave.profile.manage")
        values: dict = {
            key: value
            for key, value in data.model_dump(exclude_unset=True).items()
            if key != "default_schedule"
        }
        if data.default_schedule is not None:
            values["default_schedule"] = sched.dump(data.default_schedule)

        row = await self.settings_row()
        if row is None:
            # A first save of holiday config must still give the row its NOT NULL schedule.
            values.setdefault("default_schedule", sched.dump(await self.default_schedule()))
            row = await self.settings.create(**values)
        elif values:
            row = await self.settings.update(row, **values)
        self._settings_row = row
        return row

    # --- holidays (#47) --------------------------------------------------------- #
    async def list_holidays(
        self,
        *,
        year: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        include_inactive: bool = False,
    ) -> Sequence[LeaveHoliday]:
        """This org's calendar, by year (Settings) or by range (the agenda and the timesheet).

        A range, not only a year, because a timesheet week and a calendar month both straddle
        New Year's Eve and would otherwise need two calls to find one holiday.
        """
        await self._ensure_seeded_holidays()
        if date_from is None or date_to is None:
            target = year or _now().year
            date_from, date_to = date(target, 1, 1), date(target, 12, 31)
        stmt = self.holidays.scoped_select().where(
            LeaveHoliday.date >= date_from, LeaveHoliday.date <= date_to
        )
        if not include_inactive:
            stmt = stmt.where(LeaveHoliday.active.is_(True))
        return (
            (await self.ctx.session.execute(stmt.order_by(LeaveHoliday.date.asc())))
            .scalars()
            .all()
        )

    async def active_holidays_between(self, start: date, end: date) -> set[date]:
        """The days in ``[start, end]`` that cost no leave hours (#48). One query, one set."""
        stmt = select(LeaveHoliday.date).where(
            LeaveHoliday.org_id == self.ctx.org.id,
            LeaveHoliday.active.is_(True),
            LeaveHoliday.date >= start,
            LeaveHoliday.date <= end,
        )
        return set((await self.ctx.session.execute(stmt)).scalars().all())

    async def _ensure_seeded_holidays(self) -> None:
        """Seed this year + next once per org, the way ``_ensure_default_types`` seeds types."""
        if not self.ctx.can("leave.request.write"):
            return
        if await self.holidays.count() > 0:
            return
        settings = await self.settings_row()
        country = (settings.holiday_country if settings else None) or holidays.COUNTRY_NL
        year = _now().year
        for target in (year, year + 1):
            await self._import_year(country, target)

    async def create_holiday(self, data: LeaveHolidayCreate) -> LeaveHoliday:
        self.ctx.require("leave.holiday.write")
        if await self._on_date(data.date) is not None:
            raise AppError("conflict", "errors.leave_holiday_exists", status_code=409)
        return await self.holidays.create(
            **data.model_dump(), source=holidays.SOURCE_MANUAL, key=None
        )

    async def update_holiday(self, holiday_id: uuid.UUID, data: LeaveHolidayUpdate) -> LeaveHoliday:
        self.ctx.require("leave.holiday.write")
        holiday = await self.holidays.get_or_404(holiday_id)
        values = data.model_dump(exclude_unset=True)
        moved = values.get("date")
        if moved is not None and moved != holiday.date:
            clash = await self._on_date(moved)
            if clash is not None and clash.id != holiday.id:
                raise AppError("conflict", "errors.leave_holiday_exists", status_code=409)
        return await self.holidays.update(holiday, **values)

    async def delete_holiday(self, holiday_id: uuid.UUID) -> None:
        self.ctx.require("leave.holiday.write")
        await self.holidays.delete(await self.holidays.get_or_404(holiday_id))

    async def _on_date(self, day: date) -> LeaveHoliday | None:
        return await self.ctx.session.scalar(
            self.holidays.scoped_select().where(LeaveHoliday.date == day)
        )

    async def import_holidays(self, data: HolidayImport) -> HolidayImportResult:
        self.ctx.require("leave.holiday.write")
        settings = await self.settings_row()
        country = data.country or (settings.holiday_country if settings else None)
        return await self._import_year(country or holidays.COUNTRY_NL, data.year)

    async def _import_year(self, country: str, year: int) -> HolidayImportResult:
        """Replace this year's **generated** rows; never touch manual or deactivated ones.

        Matching is by ``key``, not by date, so Koningsdag moving from the 27th to the 26th
        *moves* the row instead of leaving two. A generated row the tenant deactivated keeps its
        ``active = false`` — re-importing must not quietly reinstate a day they work.
        """
        generated = holidays.generate(country, year)
        if not generated:
            return HolidayImportResult(created=0, updated=0, skipped=0)

        existing = (
            (
                await self.ctx.session.execute(
                    self.holidays.scoped_select().where(
                        LeaveHoliday.date >= date(year, 1, 1),
                        LeaveHoliday.date < date(year + 1, 1, 1),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_key = {row.key: row for row in existing if row.key is not None}
        by_date = {row.date: row for row in existing}

        created = updated = skipped = 0
        for holiday in generated:
            current = by_key.get(holiday.key)
            if current is not None:
                if current.date == holiday.day:
                    skipped += 1
                    continue
                # The date moved. Refuse to trample whatever now sits on the new one.
                clash = by_date.get(holiday.day)
                if clash is not None and clash.id != current.id:
                    skipped += 1
                    continue
                by_date.pop(current.date, None)
                await self.holidays.update(current, date=holiday.day)
                by_date[holiday.day] = current
                updated += 1
                continue
            if holiday.day in by_date:
                # A manual row already owns the day; the tenant's own entry wins.
                skipped += 1
                continue
            row = await self.holidays.create(
                date=holiday.day,
                name_i18n=holiday.name_i18n,
                active=True,
                source=country,
                key=holiday.key,
            )
            by_date[holiday.day] = row
            by_key[holiday.key] = row
            created += 1
        return HolidayImportResult(created=created, updated=updated, skipped=skipped)

    # --- profiles (work schedule + contract hours) ------------------------------ #
    async def _profile(self, user_id: uuid.UUID) -> LeaveProfile | None:
        return await self.ctx.session.scalar(
            self.profiles.scoped_select().where(LeaveProfile.user_id == user_id)
        )

    def _effective(
        self, profile: LeaveProfile | None, default: sched.WorkSchedule
    ) -> tuple[sched.WorkSchedule, Decimal, bool]:
        """``(schedule, hours_per_week, inherited)`` for one profile.

        ``hours_per_week`` follows the schedule when there is one. When there isn't, the
        **stored** number wins: a pre-#46 part-timer on 32 h inherits the 40 h default
        schedule for day-shape purposes, and regranting them 8 hours a week because of it
        would be a data-integrity incident, not a migration.
        """
        own = sched.parse(profile.schedule) if profile else None
        if own is not None:
            return own, sched.week_hours(own), False
        stored = profile.hours_per_week if profile else sched.week_hours(default)
        return default, stored, True

    async def profile_for(self, user_id: uuid.UUID) -> tuple[sched.WorkSchedule, Decimal, bool]:
        """``(effective schedule, contract hours, inherited)`` for one employee."""
        return self._effective(await self._profile(user_id), await self.default_schedule())

    async def effective_schedule(self, user_id: uuid.UUID) -> sched.WorkSchedule:
        """The week ``compute_hours`` measures against: this employee's own, else the org's."""
        schedule, _, _ = await self.profile_for(user_id)
        return schedule

    async def hours_per_week(self, user_id: uuid.UUID) -> Decimal:
        _, hours, _ = await self.profile_for(user_id)
        return hours

    async def list_profiles(self) -> list[tuple[uuid.UUID, Decimal, sched.WorkSchedule | None]]:
        """Every profile row for the managers' roster (``leave.profile.manage``).

        Returns each employee's **own** schedule (``None`` = follows the org default) rather
        than the merged one: the settings screen already holds the default and rendering
        "inherited" is the whole point of the distinction.
        """
        self.ctx.require("leave.profile.manage")
        rows = (await self.ctx.session.execute(self.profiles.scoped_select())).scalars().all()
        return [(p.user_id, p.hours_per_week, sched.parse(p.schedule)) for p in rows]

    async def set_profile(self, user_id: uuid.UUID, data: LeaveProfileUpdate) -> LeaveProfile:
        """Save a schedule (and derive ``hours_per_week`` from it) or, legacy, bare hours."""
        self.ctx.require("leave.profile.manage")
        await self._member_or_404(user_id)
        profile = await self._profile(user_id)

        values: dict = {}
        if "schedule" in data.model_fields_set:
            values["schedule"] = sched.dump(data.schedule) if data.schedule else None
            # Derived, never entered — including on a clear, which falls back to the org default.
            values["hours_per_week"] = sched.week_hours(
                data.schedule or await self.default_schedule()
            )
        elif data.hours_per_week is not None and (profile is None or profile.schedule is None):
            # Release-N compatibility: an older `web` posts only hours_per_week. Honour it while
            # the employee has no schedule; once they do, the schedule is the source of truth.
            values["hours_per_week"] = data.hours_per_week

        if profile is None:
            values.setdefault("hours_per_week", await self.hours_per_week(user_id))
            return await self.profiles.create(user_id=user_id, **values)
        if not values:
            return profile
        return await self.profiles.update(profile, **values)

    async def _member_or_404(self, user_id: uuid.UUID) -> None:
        membership = await self.ctx.session.scalar(
            select(Membership).where(
                Membership.org_id == self.ctx.org.id, Membership.user_id == user_id
            )
        )
        if membership is None:
            raise AppError("not_found", "errors.not_found", status_code=404)

    # --- hourly rate (#82) ------------------------------------------------------- #
    async def get_rate(self, user_id: uuid.UUID | None = None) -> tuple[uuid.UUID, Decimal | None]:
        """One employee's rate. Your own on ``leave.rate.read:own``; another's needs ``:any``.

        A rate is salary-adjacent, so this never leaks across employees: a plain member reading
        someone else is refused before any row is loaded.
        """
        uid = user_id or self.ctx.user.id
        scope = None if uid == self.ctx.user.id else "any"
        self.ctx.require("leave.rate.read", scope=scope)
        if uid != self.ctx.user.id:
            await self._member_or_404(uid)
        profile = await self._profile(uid)
        return uid, (profile.hourly_rate if profile else None)

    async def list_rates(self) -> list[tuple[uuid.UUID, Decimal | None]]:
        """Every employee's rate, for the managers' Settings → Users roster (``:any`` only)."""
        self.ctx.require("leave.rate.read", scope="any")
        rows = (await self.ctx.session.execute(self.profiles.scoped_select())).scalars().all()
        return [(p.user_id, p.hourly_rate) for p in rows]

    async def set_rate(self, user_id: uuid.UUID, rate: Decimal | None) -> LeaveProfile:
        """Set (or clear, with ``None``) an employee's rate. Admin act — ``leave.rate.write``.

        Creates the profile row if the employee has none yet (a rate can be recorded before a
        schedule is), leaving ``hours_per_week`` at the contract default until a schedule lands.
        """
        self.ctx.require("leave.rate.write")
        await self._member_or_404(user_id)
        profile = await self._profile(user_id)
        if profile is None:
            return await self.profiles.create(
                user_id=user_id,
                hours_per_week=await self.hours_per_week(user_id),
                hourly_rate=rate,
            )
        return await self.profiles.update(profile, hourly_rate=rate)

    # --- employment contracts (#65) ---------------------------------------------- #
    @staticmethod
    def _intervals_overlap(
        a_start: date, a_end: date | None, b_start: date, b_end: date | None
    ) -> bool:
        """Do two employment periods overlap? ``None`` end = open-ended (runs to ``date.max``)."""
        return a_start <= (b_end or date.max) and b_start <= (a_end or date.max)

    async def _user_contracts(self, user_id: uuid.UUID) -> list[EmploymentContract]:
        return list(
            (
                await self.ctx.session.execute(
                    self.contracts.scoped_select()
                    .where(EmploymentContract.user_id == user_id)
                    .order_by(EmploymentContract.start_date.asc())
                )
            )
            .scalars()
            .all()
        )

    async def scheduled_week(self, user_id: uuid.UUID, contract: EmploymentContract) -> Decimal:
        """Scheduled hours the ADV gap is measured against: the contract's own schedule if it
        carries one, else the employee's profile schedule, else the org default."""
        own = sched.parse(contract.schedule)
        if own is not None:
            return sched.week_hours(own)
        return await self.hours_per_week(user_id)

    async def list_contracts(
        self, user_id: uuid.UUID | None = None, *, all_users: bool = False
    ) -> list[tuple[EmploymentContract, Decimal]]:
        """Contracts + their scheduled week. Managers see anyone's (or everyone's, for the
        roster); a member only their own."""
        if all_users:
            self.ctx.require("leave.profile.manage")
            contracts = (
                (
                    await self.ctx.session.execute(
                        self.contracts.scoped_select().order_by(
                            EmploymentContract.user_id,
                            EmploymentContract.start_date.asc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [(c, await self.scheduled_week(c.user_id, c)) for c in contracts]
        uid = user_id or self.ctx.user.id
        if uid != self.ctx.user.id:
            self.ctx.require("leave.profile.manage")
        contracts = await self._user_contracts(uid)
        return [(c, await self.scheduled_week(uid, c)) for c in contracts]

    async def create_contract(self, data: EmploymentContractCreate) -> EmploymentContract:
        self.ctx.require("leave.profile.manage")
        await self._member_or_404(data.user_id)
        self._validate_contract_dates(data.start_date, data.end_date)
        await self._ensure_no_contract_overlap(
            data.user_id, data.start_date, data.end_date, exclude_id=None
        )
        values = data.model_dump(exclude={"schedule"})
        values["schedule"] = sched.dump(data.schedule) if data.schedule else None
        contract = await self.contracts.create(**values)
        # #105: the contract and the entitlements it earns commit atomically — no separate,
        # easily-forgotten "Genereer" step before the new hire has a balance.
        await self._seed_after_contract_change(contract.user_id)
        return contract

    async def update_contract(
        self, contract_id: uuid.UUID, data: EmploymentContractUpdate
    ) -> EmploymentContract:
        self.ctx.require("leave.profile.manage")
        contract = await self.contracts.get_or_404(contract_id)
        values = data.model_dump(exclude_unset=True, exclude={"schedule"})
        if "schedule" in data.model_fields_set:
            values["schedule"] = sched.dump(data.schedule) if data.schedule else None
        start = values.get("start_date", contract.start_date)
        end = values.get("end_date", contract.end_date)
        self._validate_contract_dates(start, end)
        await self._ensure_no_contract_overlap(
            contract.user_id, start, end, exclude_id=contract.id
        )
        updated = await self.contracts.update(contract, **values)
        # A corrected period can pull a year into scope that had nothing for this user yet.
        # Missing rows only — never a recalculation of what was already granted (§14).
        await self._seed_after_contract_change(updated.user_id)
        return updated

    async def delete_contract(self, contract_id: uuid.UUID) -> None:
        self.ctx.require("leave.profile.manage")
        await self.contracts.delete(await self.contracts.get_or_404(contract_id))

    def _validate_contract_dates(self, start: date, end: date | None) -> None:
        if end is not None and end < start:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"end_date": "errors.leave_end_before_start"},
            )

    async def _ensure_no_contract_overlap(
        self, user_id: uuid.UUID, start: date, end: date | None, *, exclude_id: uuid.UUID | None
    ) -> None:
        """Contracts for one user must not overlap (enforced here — see the model docstring)."""
        for other in await self._user_contracts(user_id):
            if exclude_id is not None and other.id == exclude_id:
                continue
            if self._intervals_overlap(start, end, other.start_date, other.end_date):
                raise AppError(
                    "conflict", "errors.leave_contract_overlap", status_code=409
                )

    def _contracts_in_year(
        self, contracts: Sequence[EmploymentContract], year: int
    ) -> list[EmploymentContract]:
        jan, dec = date(year, 1, 1), date(year, 12, 31)
        return [
            c
            for c in contracts
            if self._intervals_overlap(c.start_date, c.end_date, jan, dec)
        ]

    @staticmethod
    def _year_overlap_days(contract: EmploymentContract, year: int) -> int:
        """Days of ``year`` this contract covers (inclusive)."""
        start = max(contract.start_date, date(year, 1, 1))
        end = min(contract.end_date or date(year, 12, 31), date(year, 12, 31))
        return (end - start).days + 1 if end >= start else 0

    @staticmethod
    def _round_half_day(hours: Decimal, avg_day_hours: Decimal) -> Decimal:
        """ADV accrues in awkward totals (104,3 h); round to the nearest bookable half day."""
        half = avg_day_hours / 2
        if half <= 0:
            return hours.quantize(Decimal("0.01"))
        steps = (hours / half).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return (steps * half).quantize(Decimal("0.01"))

    # --- recurring rostered free days / ADV (#107) --------------------------------- #
    async def list_recurring(
        self, *, user_id: uuid.UUID | None = None
    ) -> Sequence[LeaveRecurringDay]:
        """The org's patterns (managers' roster), optionally narrowed to one employee."""
        self.ctx.require("leave.profile.manage")
        stmt = self.recurring.scoped_select().order_by(
            LeaveRecurringDay.user_id, LeaveRecurringDay.anchor_date
        )
        if user_id is not None:
            stmt = stmt.where(LeaveRecurringDay.user_id == user_id)
        return (await self.ctx.session.execute(stmt)).scalars().all()

    async def create_recurring(self, data: LeaveRecurringDayCreate) -> tuple[LeaveRecurringDay, int]:
        """Define a pattern and immediately lay its days onto the calendar.

        A pattern is employment data, so it is managed with the schedules and contracts
        (``leave.profile.manage``) — not a self-service member act.
        """
        self.ctx.require("leave.profile.manage")
        await self._member_or_404(data.user_id)
        leave_type = await self.types.get_or_404(data.leave_type_id)
        if not leave_type.active:
            raise AppError("validation", "errors.validation", status_code=422)
        pattern = await self.recurring.create(**data.model_dump())
        created = await self.generate_recurring_days(pattern_id=pattern.id)
        return pattern, created

    async def update_recurring(
        self, recurring_id: uuid.UUID, data: LeaveRecurringDayUpdate
    ) -> tuple[LeaveRecurringDay, int]:
        self.ctx.require("leave.profile.manage")
        pattern = await self.recurring.get_or_404(recurring_id)
        values = data.model_dump(exclude_unset=True)
        if "leave_type_id" in values:
            leave_type = await self.types.get_or_404(values["leave_type_id"])
            if not leave_type.active:
                raise AppError("validation", "errors.validation", status_code=422)
        pattern = await self.recurring.update(pattern, **values)
        created = 0
        if pattern.active:
            created = await self.generate_recurring_days(pattern_id=pattern.id)
        return pattern, created

    async def delete_recurring(self, recurring_id: uuid.UUID) -> None:
        """Delete the pattern; days already placed stay (FK is SET NULL) — they are real,
        individually cancellable leave the employee may have planned around."""
        self.ctx.require("leave.profile.manage")
        await self.recurring.delete(await self.recurring.get_or_404(recurring_id))

    @staticmethod
    def _add_months(day: date, months: int) -> date:
        month = day.month - 1 + months
        year = day.year + month // 12
        month = month % 12 + 1
        last_day = monthrange(year, month)[1]
        return date(year, month, min(day.day, last_day))

    async def _recurring_horizon(self, user_id: uuid.UUID, today: date) -> date:
        """How far this employee's free days are generated ahead (#107).

        A **fixed-term** contract is filled to its end date — the whole term the tenant
        entered, and never a day past it: a free day after employment ends is meaningless,
        and one before the end is exactly what the pattern promises. An open-ended contract
        (or none) gets a rolling look-ahead of ``leave_settings.recurring_horizon_months``
        (default 12), extended by the monthly cron. Balance caps still bound either horizon:
        a year whose pot doesn't exist yet simply generates nothing until it is seeded.
        """
        row = await self.settings_row()
        months = row.recurring_horizon_months if row else 12
        rolling = self._add_months(today, months)
        contracts = await self._user_contracts(user_id)
        if not contracts or any(c.end_date is None for c in contracts):
            return rolling
        return max(c.end_date for c in contracts if c.end_date is not None)

    async def generate_recurring_days(self, *, pattern_id: uuid.UUID | None = None) -> int:
        """Lay every active pattern's upcoming occurrences onto the calendar (#107).

        Idempotent by construction: an occurrence is *spent* once any request row carries its
        ``(recurring_day_id, recurring_date)`` — moved, cancelled or still standing — so a day
        the employee shifted away never reappears on its pattern date. Occurrences are skipped
        (never queued elsewhere) when the day is worth no hours (holiday, not scheduled), when
        another occupying request already covers it, or when a balance-tracked type's pot has
        less than the day costs — the pattern must not generate more free hours than the
        scheduled−contract gap earned (§14). A skipped-for-balance occurrence is retried on
        the next run, so a far-out day materializes the moment its year's pot is seeded.
        Runs from pattern saves and the monthly cron.
        """
        stmt = self.recurring.scoped_select().where(LeaveRecurringDay.active.is_(True))
        if pattern_id is not None:
            stmt = stmt.where(LeaveRecurringDay.id == pattern_id)
        patterns = (await self.ctx.session.execute(stmt)).scalars().all()
        if not patterns:
            return 0
        # Holidays seed lazily on first *listing* — but in a fresh org this generator can run
        # before anything ever listed them, and an unseeded calendar would happily place a
        # free day on Eerste Kerstdag. Seed first; `compute_hours` then knows what to skip.
        await self._ensure_seeded_holidays()

        today = await self._org_today()
        created = 0
        for pattern in patterns:
            horizon = await self._recurring_horizon(pattern.user_id, today)
            leave_type = await self.types.get_or_404(pattern.leave_type_id)
            step = timedelta(weeks=pattern.interval_weeks)
            occurrence = pattern.anchor_date
            # Never backfill: the first generated day is the first occurrence from today on.
            if occurrence < today:
                behind = (today - occurrence).days
                cycles = -(-behind // (pattern.interval_weeks * 7))  # ceil
                occurrence += cycles * step

            spent = set(
                (
                    await self.ctx.session.execute(
                        select(LeaveRequest.recurring_date).where(
                            LeaveRequest.org_id == self.ctx.org.id,
                            LeaveRequest.recurring_day_id == pattern.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            # One balance read per (year) the horizon touches, decremented locally as days
            # land — not one query per occurrence (docs/PERFORMANCE.md).
            remaining_by_year: dict[int, Decimal] = {}

            while occurrence <= horizon:
                day = occurrence
                occurrence += step
                if day in spent:
                    continue
                hours, _ = await self.compute_hours(pattern.user_id, day, None, day, None)
                if hours <= 0:
                    continue  # holiday or not a scheduled working day — meaningless free day
                overlap = (
                    await self.ctx.session.execute(
                        self.requests.scoped_select()
                        .where(
                            LeaveRequest.user_id == pattern.user_id,
                            LeaveRequest.status.in_(_OCCUPYING),
                            LeaveRequest.start_date <= day,
                            LeaveRequest.end_date >= day,
                        )
                        .limit(1)
                    )
                ).scalars().first()
                if overlap is not None:
                    continue
                if leave_type.tracks_balance:
                    if day.year not in remaining_by_year:
                        balances = {
                            b.leave_type_id: b
                            for b in await self.balances(year=day.year, user_id=pattern.user_id)
                        }
                        balance = balances.get(leave_type.id)
                        remaining_by_year[day.year] = (
                            balance.remaining_hours if balance else Decimal(0)
                        )
                    if remaining_by_year[day.year] < hours:
                        continue  # the gap earned no more free hours this year
                    remaining_by_year[day.year] -= hours
                # An auto-approved registration, like a sick report: the pattern was a
                # manager's act, and the employee moves individual days within the rules.
                await self.requests.create(
                    user_id=pattern.user_id,
                    leave_type_id=pattern.leave_type_id,
                    start_date=day,
                    end_date=day,
                    hours=hours,
                    note=pattern.note,
                    status=LeaveRequestStatus.APPROVED.value,
                    recurring_day_id=pattern.id,
                    recurring_date=day,
                )
                created += 1
        return created

    # --- entitlements ------------------------------------------------------------ #
    async def _seed_after_contract_change(self, user_id: uuid.UUID) -> None:
        """Fill this user's missing entitlements after a contract create/correct (#105).

        Which years: the current org-local year, plus any **future** year the org has already
        generated for other staff — a hire in December also gets next year when next year
        already exists for everyone else. Past years are never backfilled, and nothing existing
        is touched. Runs in the contract write's own transaction and is deliberately not gated
        on ``leave.entitlement.write``: a profile-manager adding a contract was already allowed
        to make this write, and the entitlements are its consequence.
        """
        current = (await self._org_today()).year
        future_years = (
            (
                await self.ctx.session.execute(
                    select(LeaveEntitlement.year)
                    .where(
                        LeaveEntitlement.org_id == self.ctx.org.id,
                        LeaveEntitlement.year > current,
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        # Only years this user's contracts actually cover: without the overlap filter, a hire
        # whose contract starts next January would fall into the contract-less fallback for the
        # *current* year and be granted a full pot for a year they don't work.
        contracts = await self._user_contracts(user_id)
        for year in sorted({current, *future_years}):
            if self._contracts_in_year(contracts, year):
                await self.seed_entitlements(year, only_users={user_id})

    async def _ensure_entitlements(self, user_id: uuid.UUID, year: int) -> None:
        """Seed one user's pot on first touch of a year that has nothing for them yet (#108).

        "Book next January while it's still July" must not die on a pot nobody generated. Only
        the current or the next org-local year (never a backfill of history), only when the user
        has **no** entitlement rows for that year at all — an admin who deliberately zeroed a
        grant keeps their zero — and only for callers who can hold leave in the first place
        (``leave.request.write``), so a read-only client role never writes on read.
        """
        if not self.ctx.can("leave.request.write"):
            return
        has_rows = (
            await self.ctx.session.execute(
                self.entitlements.scoped_select()
                .where(
                    LeaveEntitlement.user_id == user_id,
                    LeaveEntitlement.year == year,
                )
                .limit(1)
            )
        ).scalars().first() is not None
        if has_rows:
            return
        current = (await self._org_today()).year
        if year < current or year > current + 1:
            return
        await self.seed_entitlements(year, only_users={user_id})

    async def list_entitlements(
        self, *, year: int, user_id: uuid.UUID | None = None
    ) -> Sequence[LeaveEntitlement]:
        uid = self._effective_user_id(user_id)
        stmt = self.entitlements.scoped_select().where(LeaveEntitlement.year == year)
        if not (self.ctx.can("leave.entitlement.read", scope="any") and user_id is None):
            stmt = stmt.where(LeaveEntitlement.user_id == uid)
        return (await self.ctx.session.execute(stmt)).scalars().all()

    async def upsert_entitlement(self, data: LeaveEntitlementUpsert) -> LeaveEntitlement:
        self.ctx.require("leave.entitlement.write")
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
        """Create missing entitlements for a year (#65) — the guarded bulk endpoint."""
        self.ctx.require("leave.entitlement.write")
        return await self.seed_entitlements(year)

    async def seed_entitlements(
        self, year: int, *, only_users: set[uuid.UUID] | None = None
    ) -> int:
        """The generation core: create **missing** entitlements for a year (#65).

        Two families of balance-tracked type:
          * ``default_weeks`` types (statutory/extra vacation) → ``weeks × contract hours``,
            **prorated** over the days each contract covers of the year;
          * ``accrues_schedule_gap`` types (roostervrije tijd / ADV) → the scheduled-minus-contract
            hours gap × the weeks the contract runs in the year, rounded to the nearest half day.

        Contract hours are the legal number, not the scheduled week: a 38-hour contract worked as
        40 scheduled hours gets ``4 × 38`` statutory hours and ``2 × weeks`` of ADV. An employee
        with **no** contract falls back to the pre-#65 behaviour — a full year on their *scheduled*
        hours — so upgrading moves nobody's balance and needs no contract backfill.

        "Who is staff for year N" is anyone with a contract overlapping the year, unioned with the
        legacy ``time.entry.write`` holders so a contract-less org still generates.

        **Deliberately not permission-checked** (#105): besides the guarded bulk endpoint it runs
        as a *side effect of writes the caller was already allowed to make* — adding a contract
        (``leave.profile.manage``), or reading a balance whose pot simply doesn't exist yet — the
        same principle as the activity log (CLAUDE.md §16). Idempotent and non-destructive: only
        missing rows are created, existing or manually-adjusted ones are never touched.
        """
        all_types = await self.list_types()
        weeks_types = [
            t for t in all_types if t.tracks_balance and t.default_weeks is not None
        ]
        gap_types = [t for t in all_types if t.tracks_balance and t.accrues_schedule_gap]

        legacy_staff = set(
            (
                await self.ctx.session.execute(
                    permission_holder_ids(self.ctx.org.id, "time.entry.write")
                )
            )
            .scalars()
            .all()
        )
        contract_rows = (
            (await self.ctx.session.execute(self.contracts.scoped_select())).scalars().all()
        )
        contracts_by_user: dict[uuid.UUID, list[EmploymentContract]] = {}
        for contract in contract_rows:
            contracts_by_user.setdefault(contract.user_id, []).append(contract)
        contract_staff = {
            uid
            for uid, cs in contracts_by_user.items()
            if self._contracts_in_year(cs, year)
        }
        staff = legacy_staff | contract_staff
        if only_users is not None:
            staff &= only_users

        # Straight off the repo, not ``list_entitlements``: that read narrows to the caller's own
        # rows for anyone without ``leave.entitlement.read:any``, and a half-blind "existing" set
        # here would mean duplicate rows (a unique-violation) for exactly those callers.
        existing_rows = (
            (
                await self.ctx.session.execute(
                    self.entitlements.scoped_select().where(LeaveEntitlement.year == year)
                )
            )
            .scalars()
            .all()
        )
        existing = {(e.user_id, e.leave_type_id) for e in existing_rows}
        default = await self.default_schedule()
        rows = (await self.ctx.session.execute(self.profiles.scoped_select())).scalars().all()
        by_user = {p.user_id: p for p in rows}
        year_days = (date(year, 12, 31) - date(year, 1, 1)).days + 1

        created = 0
        for user_id in staff:
            contracts_year = self._contracts_in_year(contracts_by_user.get(user_id, []), year)
            schedule, scheduled_week, _ = self._effective(by_user.get(user_id), default)
            avg_day = sched.average_day_hours(schedule)

            for leave_type in weeks_types:
                if (user_id, leave_type.id) in existing:
                    continue
                weeks = leave_type.default_weeks or Decimal(0)
                if contracts_year:
                    hours = sum(
                        (
                            weeks
                            * c.contract_hours_per_week
                            * Decimal(self._year_overlap_days(c, year))
                            / Decimal(year_days)
                            for c in contracts_year
                        ),
                        Decimal(0),
                    ).quantize(Decimal("0.01"))
                else:
                    hours = (weeks * scheduled_week).quantize(Decimal("0.01"))
                await self.entitlements.create(
                    user_id=user_id, leave_type_id=leave_type.id, year=year, hours=hours
                )
                created += 1

            for leave_type in gap_types:
                if (user_id, leave_type.id) in existing:
                    continue
                total = Decimal(0)
                for c in contracts_year:
                    gap = (await self.scheduled_week(user_id, c)) - c.contract_hours_per_week
                    if gap <= 0:
                        continue
                    total += gap * Decimal(self._year_overlap_days(c, year)) / Decimal(7)
                hours = self._round_half_day(total, avg_day) if total > 0 else Decimal("0.00")
                if hours <= 0:
                    # No scheduling gap → no ADV. Don't clutter the balance with a zero row.
                    continue
                await self.entitlements.create(
                    user_id=user_id, leave_type_id=leave_type.id, year=year, hours=hours
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
        # First touch of an ungenerated current/next-year pot seeds it (#108), so "book next
        # summer in December" finds a balance instead of a zero.
        await self._ensure_entitlements(uid, year)
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

    # --- the hour calculation (#48) --------------------------------------------------- #
    def _breakdown(
        self,
        *,
        schedule: sched.WorkSchedule,
        holidays_off: set[date],
        start_date: date,
        start_time: time | None,
        end_date: date,
        end_time: time | None,
    ) -> list[tuple[date, int, str | None]]:
        """Per-day worked minutes, and why a day is worth nothing when it is.

        For each date: not a scheduled working day → 0; an active holiday → 0; otherwise the
        day's scheduled window intersected with the requested one, minus every break it overlaps.
        The intersection *is* the clamp — "from 08:00" on an 08:30 day means "from the start".
        """
        rows: list[tuple[date, int, str | None]] = []
        day = start_date
        while day <= end_date:
            work_day = schedule.day(day.weekday())
            if work_day is None or sched.day_minutes(work_day) == 0:
                rows.append((day, 0, "not_scheduled"))
            elif day in holidays_off:
                rows.append((day, 0, "holiday"))
            else:
                low = sched.to_minutes(start_time) if (day == start_date and start_time) else 0
                high = sched.to_minutes(end_time) if (day == end_date and end_time) else _DAY
                minutes = sched.day_minutes(work_day, (low, high))
                rows.append((day, minutes, None if minutes else "outside_hours"))
            day += timedelta(days=1)
        return rows

    async def compute_hours(
        self,
        user_id: uuid.UUID,
        start_date: date,
        start_time: time | None,
        end_date: date,
        end_time: time | None,
    ) -> tuple[Decimal, list[LeaveDayHours]]:
        """``(total hours, per-day breakdown)`` — the one place a leave hour is decided.

        Rounds to two decimals **once**, on the summed minutes: rounding each day and adding the
        results is how a 40-hour week becomes 39,99.
        """
        schedule = await self.effective_schedule(user_id)
        holidays_off = await self.active_holidays_between(start_date, end_date)
        rows = self._breakdown(
            schedule=schedule,
            holidays_off=holidays_off,
            start_date=start_date,
            start_time=start_time,
            end_date=end_date,
            end_time=end_time,
        )
        total = sched.to_hours(sum(minutes for _, minutes, _ in rows))
        breakdown = [
            LeaveDayHours(date=day, hours=sched.to_hours(minutes), reason=reason)
            for day, minutes, reason in rows
        ]
        return total, breakdown

    async def _org_today(self) -> date:
        """Today in the org's own zone (CLAUDE.md §8). "The past" is a local-calendar concept:
        a request touches the past when it reaches before *this* date, not before UTC midnight."""
        return datetime.now(await org_zoneinfo(self.ctx.session, self.ctx.org.id)).date()

    async def _self_approval_allowed(self) -> bool:
        """May the caller act as the approver of their **own** leave (#110)?

        Tenant policy, not code (§14): ``leave_settings.self_approval``, off by default —
        an approver's own request should be another approver's call. One runtime exception
        either way: when no *other* member holds ``leave.request.approve``, the sole approver
        may self-manage — otherwise a one-person agency can never approve anything at all.
        """
        if self._self_approval is None:
            row = await self.settings_row()
            if row is not None and row.self_approval:
                self._self_approval = True
            else:
                managers = await self._managers()
                self._self_approval = not any(m != self.ctx.user.id for m in managers)
        return self._self_approval

    async def _acts_as_approver(self, owner_id: uuid.UUID) -> bool:
        """Approver powers over a request: always on someone else's; on your own only when
        the org's self-approval policy (or the sole-approver fallback) says so (#110)."""
        if not self.ctx.can("leave.request.approve"):
            return False
        return owner_id != self.ctx.user.id or await self._self_approval_allowed()

    async def _may_backdate(self, *, own: bool) -> bool:
        """Only a caller who may act on *anyone's* leave may reach into the past (#65 §6).

        Leave registered on someone's behalf (a retroactive ``ziekmelding``) is past-dated by
        nature, so ``leave.request.write:any`` may backdate; a member on ``:own`` may not create,
        move or cancel leave that touches the past — the closed calendar is not theirs to
        rewrite. The same lock covers an approver's **own** leave while self-approval is off
        (#110): rewriting your own history is precisely what the second pair of eyes is for.
        """
        if not self.ctx.can("leave.request.write", scope="any"):
            return False
        return not own or await self._self_approval_allowed()

    async def _ensure_may_backdate(self, touches_past: bool, *, own: bool) -> None:
        if touches_past and not await self._may_backdate(own=own):
            raise AppError("past_locked", "errors.leave_past_locked", status_code=403)

    async def preview(self, data: LeaveRequestPreview) -> LeavePreviewResult:
        """What the form shows before submitting — the number the server will store."""
        uid = self._effective_user_id(data.user_id)
        if uid != self.ctx.user.id:
            # 404, not "the org default schedule", when the id belongs to another tenant.
            await self._member_or_404(uid)
        self._validate_span(data.start_date, data.start_time, data.end_date, data.end_time)
        hours, breakdown = await self.compute_hours(
            uid, data.start_date, data.start_time, data.end_date, data.end_time
        )
        per_day = sched.average_day_hours(await self.effective_schedule(uid))
        days = (hours / per_day).quantize(Decimal("0.01")) if per_day else Decimal("0")
        # Tell the form whether saving would need (re-)approval (#72): a past span always does; a
        # future one only if the chosen type requires approval. The form knows the request's
        # current status, so it decides whether to warn "this moves it back to pending".
        touches_past = data.start_date < await self._org_today()
        requires_approval = touches_past
        remaining: Decimal | None = None
        if data.leave_type_id is not None:
            leave_type = await self.types.get_or_404(data.leave_type_id)
            requires_approval = leave_type.requires_approval or touches_past
            if leave_type.tracks_balance:
                # The server computes the over-request warning's input, not the browser: the
                # form's own balance props belong to the *viewer*, which on the manager's
                # register-for-someone flow is the wrong employee (#109).
                year = data.start_date.year
                balances = {
                    b.leave_type_id: b for b in await self.balances(year=year, user_id=uid)
                }
                balance = balances.get(leave_type.id)
                remaining = balance.remaining_hours if balance else Decimal(0)
                if data.request_id is not None:
                    # Editing: the request's own current hours still occupy the balance, so
                    # they are given back before the form compares against the new span.
                    current = await self.requests.get_or_404(data.request_id)
                    if (
                        current.user_id == uid
                        and current.leave_type_id == leave_type.id
                        and current.start_date.year == year
                        and current.status in _OCCUPYING
                    ):
                        remaining += Decimal(current.hours)
        return LeavePreviewResult(
            hours=hours,
            days=days,
            breakdown=breakdown,
            requires_approval=requires_approval,
            touches_past=touches_past,
            remaining_hours=remaining,
        )

    def _validate_span(
        self,
        start_date: date,
        start_time: time | None,
        end_date: date,
        end_time: time | None,
    ) -> None:
        if end_date < start_date:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"end_date": "errors.leave_end_before_start"},
            )
        # Only on a *same-day* request: `Thu 15:00 → Fri 14:00` is an ordinary overnight span.
        if start_date == end_date and start_time and end_time and end_time <= start_time:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"end_time": "errors.leave_end_time_before_start"},
            )

    async def _resolve_hours(
        self,
        *,
        user_id: uuid.UUID,
        start_date: date,
        start_time: time | None,
        end_date: date,
        end_time: time | None,
        override: Decimal | None,
    ) -> Decimal:
        """The stored ``hours``: the manager's number when there is one, else the computed one.

        Whether the caller is *allowed* to set an override is checked where the intent is
        visible — at the call site. Re-checking it here would 403 a member who merely edits the
        note on a request a manager overrode last week.
        """
        self._validate_span(start_date, start_time, end_date, end_time)
        if override is not None:
            return override
        hours, _ = await self.compute_hours(user_id, start_date, start_time, end_date, end_time)
        if hours <= 0:
            # A Saturday, a holiday, or half an hour that lands entirely inside lunch. Say so
            # rather than storing a zero-hour request nobody will ever notice.
            raise AppError(
                "no_working_hours", "errors.leave_no_working_hours", status_code=422
            )
        return hours

    # --- requests ------------------------------------------------------------------- #
    async def _validate_request(
        self,
        *,
        user_id: uuid.UUID,
        start_date: date,
        end_date: date,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        """Overlap is the one hard request-level error left here.

        Over-requests on balance-tracked types used to 400 — that blocked advance/borrowed leave
        and every next-year request over a not-yet-generated pot, and the manager never even saw
        the ask. Now the request submits, the balance simply reads negative, and both sides are
        *warned*: the form via ``preview.remaining_hours``, the approver on the pending list. The
        decision belongs to the manager, not to a blanket block (#109, CLAUDE.md §14).
        """
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

    async def create(self, data: LeaveRequestCreate) -> LeaveRequest:
        self.ctx.require(
            "leave.request.write",
            scope=None if data.user_id in (None, self.ctx.user.id) else "any",
        )
        uid = self._effective_user_id(data.user_id)
        leave_type = await self.types.get_or_404(data.leave_type_id)
        if not leave_type.active:
            raise AppError("validation", "errors.validation", status_code=422)
        # A member may not book leave in the past; a manager registering it (a sick call) may (#65).
        await self._ensure_may_backdate(
            data.start_date < await self._org_today(), own=uid == self.ctx.user.id
        )
        # Setting `hours` by hand is an approver's act, and it is recorded as one.
        if data.hours_override is not None:
            self.ctx.require("leave.request.approve")
        hours = await self._resolve_hours(
            user_id=uid,
            start_date=data.start_date,
            start_time=data.start_time,
            end_date=data.end_date,
            end_time=data.end_time,
            override=data.hours_override,
        )
        await self._validate_request(
            user_id=uid,
            start_date=data.start_date,
            end_date=data.end_date,
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
            start_time=data.start_time,
            end_date=data.end_date,
            end_time=data.end_time,
            hours=hours,
            hours_override=data.hours_override,
            hours_override_by_user_id=self.ctx.user.id if data.hours_override else None,
            note=data.note,
            status=status,
        )
        # Only a request that actually waits on somebody is worth interrupting them for; a
        # registration (sick leave) is already decided (issue #16). The requester is never
        # among the recipients — an approver asking for leave doesn't need a note to self.
        if request.status == LeaveRequestStatus.PENDING.value:
            await self._emit_leave(
                "leave.requested", request, await self._approvers_for(request)
            )
        return request

    async def _managers(self) -> list[uuid.UUID]:
        """Who may approve leave here: the holders of ``leave.request.approve`` (issue #19)."""
        return list(
            (
                await self.ctx.session.execute(
                    permission_holder_ids(self.ctx.org.id, "leave.request.approve")
                )
            ).scalars()
        )

    async def _approvers_for(self, request: LeaveRequest) -> list[uuid.UUID]:
        """The approvers a pending request notifies: everyone who may decide it, minus its
        own requester — whether they may self-approve or not, a note to self is noise."""
        return [m for m in await self._managers() if m != request.user_id]

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
        """Edit a request, re-triggering approval when the rule says so (#72).

        An edit needs (re-)approval **if the leave type requires approval, or the edit touches
        the past** — otherwise the owner may change their own future, self-service leave freely.
        So the approved-lock is relaxed: an owner may now edit their own approved request, and
        the resulting status is decided here rather than being frozen at ``approved``.
        """
        request = await self._owned_or_404(request_id)
        self._ensure_writable(request)
        if request.status not in _OCCUPYING:
            raise AppError("conflict", "errors.leave_decided", status_code=409)
        # An approver's edit stands as its own approval — except on their *own* request while
        # self-approval is off (#110): there they are an ordinary owner and the edit bounces.
        acts_as_approver = await self._acts_as_approver(request.user_id)
        values = data.model_dump(exclude_unset=True)
        leave_type = await self.types.get_or_404(
            values.get("leave_type_id", request.leave_type_id)
        )
        start_date = values.get("start_date", request.start_date)
        start_time = values.get("start_time", request.start_time)
        end_date = values.get("end_date", request.end_date)
        end_time = values.get("end_time", request.end_time)
        override = values.get("hours_override", request.hours_override)
        # Setting or clearing the override is an approver's act; leaving a stored one alone isn't.
        if "hours_override" in data.model_fields_set:
            self.ctx.require("leave.request.approve")

        # Recomputed on every edit, so a request moved into Kerst week gets cheaper (#48).
        values["hours"] = await self._resolve_hours(
            user_id=request.user_id,
            start_date=start_date,
            start_time=start_time,
            end_date=end_date,
            end_time=end_time,
            override=override,
        )
        if "hours_override" in data.model_fields_set:
            values["hours_override"] = data.hours_override
            values["hours_override_by_user_id"] = (
                self.ctx.user.id if data.hours_override is not None else None
            )

        await self._validate_request(
            user_id=request.user_id,
            start_date=start_date,
            end_date=end_date,
            exclude_id=request.id,
        )

        # --- re-approval decision (#72) ------------------------------------------------ #
        # Did the edit change anything a manager would need to weigh again? A bare note edit does
        # not — it must never bounce an approved request back to pending.
        approval_relevant = (
            leave_type.id != request.leave_type_id
            or start_date != request.start_date
            or start_time != request.start_time
            or end_date != request.end_date
            or end_time != request.end_time
            or values["hours"] != request.hours
            or override != request.hours_override
        )
        # The span touches the past if the *result* reaches before today, or the *original* already
        # did — so you can neither slip a change into history nor quietly pull one out of it.
        today = await self._org_today()
        touches_past = start_date < today or request.start_date < today
        needs_approval = leave_type.requires_approval or touches_past
        # Moving a request into or within the past is a manager's act (#65 §6). A note-only edit
        # leaves the span alone and is never blocked — that is why the guard keys off the span
        # changing, not merely off the request already touching the past.
        span_changed = (
            start_date != request.start_date
            or start_time != request.start_time
            or end_date != request.end_date
            or end_time != request.end_time
        )
        if span_changed:
            await self._ensure_may_backdate(
                touches_past, own=request.user_id == self.ctx.user.id
            )

        bounce = (
            request.status == LeaveRequestStatus.APPROVED.value
            and approval_relevant
            and needs_approval
            and not acts_as_approver
        )
        if bounce:
            # A resubmission, not a self-approval: the owner's edit sends an approved request back
            # through approval. Reuses the whole pending path (decide/notify/balance), and pending
            # still occupies the balance, so no balance regression.
            values["status"] = LeaveRequestStatus.PENDING.value
            values["decided_by_user_id"] = None
            values["decided_at"] = None
            values["decision_note"] = None

        updated = await self.requests.update(request, **values)
        if bounce:
            await self._emit_leave("leave.requested", updated, await self._approvers_for(updated))
        return updated

    async def decide(
        self, request_id: uuid.UUID, *, approved: bool, note: str | None = None
    ) -> LeaveRequest:
        self.ctx.require("leave.request.approve")
        request = await self.requests.get_or_404(request_id)
        if request.status != LeaveRequestStatus.PENDING.value:
            raise AppError("conflict", "errors.leave_decided", status_code=409)
        # Deciding your own request is self-approval; while the org forbids it, another
        # approver must take this one (#110). The edit path enforces the same policy, or the
        # control would be trivially sidestepped by editing instead of deciding.
        if request.user_id == self.ctx.user.id and not await self._self_approval_allowed():
            raise AppError("self_approval", "errors.leave_self_approval", status_code=403)
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
        """Cancel a request. Pending: the requester may. Approved: an approver may — and so may
        the owner, but only for their own *self-service, future* leave (#72).

        The same rule as an edit governs the approved case: an approved request is the owner's to
        cancel when it would not need approval — an auto-approve type (a free day, a sick report)
        that does not touch the past. "Moving" an ADV free day is exactly this: cancel one, create
        another (#65). Cancelling approved leave that needed approval, or that reaches into the
        past, stays a manager's call.
        """
        request = await self._owned_or_404(request_id)
        self._ensure_writable(request)
        own = request.user_id == self.ctx.user.id
        # Cancelling past-dated leave rewrites the closed calendar — a manager's act (#65 §6),
        # and not one an approver may perform on their *own* leave while self-approval is off.
        await self._ensure_may_backdate(
            request.start_date < await self._org_today(), own=own
        )
        if request.status == LeaveRequestStatus.APPROVED.value:
            if not await self._acts_as_approver(request.user_id):
                leave_type = await self.types.get_or_404(request.leave_type_id)
                if leave_type.requires_approval:
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
            self.ctx.require("leave.request.read", scope="any")
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
    def _shaped_days(
        self,
        request: LeaveRequest,
        schedule: sched.WorkSchedule,
        holidays_off: set[date],
    ) -> list[LeaveDayHours]:
        """The request's stored hours, laid out over its days in the shape the schedule gives.

        Two rules that fight, and how they're reconciled: the **stored total** is what the
        employee and their manager agreed to, and approved requests are never retroactively
        recalculated (#47, #48). But the **shape** — 2 h Thursday, 5 h Friday — has to come from
        the schedule, because spreading the total evenly is exactly the bug this replaces.

        So: compute the shape, then distribute the stored total in proportion to it. For a
        request created after this landed the factor is 1 and the days *are* the computation.
        Only a request whose schedule or holidays changed underneath it gets scaled, and there
        the total still reads as what was agreed. A request with a manager override, or one whose
        every day has since become a holiday, has no shape at all — it lands on the start date.
        """
        rows = self._breakdown(
            schedule=schedule,
            holidays_off=holidays_off,
            start_date=request.start_date,
            start_time=request.start_time,
            end_date=request.end_date,
            end_time=request.end_time,
        )
        total_minutes = sum(minutes for _, minutes, _ in rows)
        stored = Decimal(request.hours)
        if total_minutes == 0:
            return [
                LeaveDayHours(
                    date=day,
                    hours=stored if day == request.start_date else Decimal("0.00"),
                    reason=reason,
                )
                for day, _, reason in rows
            ]

        days = [
            LeaveDayHours(
                date=day,
                hours=(stored * minutes / total_minutes).quantize(Decimal("0.01")),
                reason=reason,
            )
            for day, minutes, reason in rows
        ]
        # Push the rounding residue onto the biggest day, so the row still sums to `hours`.
        residue = stored - sum((d.hours for d in days), Decimal("0"))
        if residue:
            biggest = max(days, key=lambda d: d.hours)
            biggest.hours += residue
        return days

    async def team(
        self, *, date_from: date, date_to: date, user_id: uuid.UUID | None = None
    ) -> list[TeamLeaveItem]:
        """Occupying absences overlapping the range, with names — the calendar/timesheet feed.

        Open to all staff (who's off is normal team-visible information in an agency). A
        ``client`` role holds no ``leave.request.read`` at all, so the route already refused it.

        Three extra reads, not three per row: every profile, the org default and the holidays in
        range are fetched once and the per-day shapes computed in Python (docs/PERFORMANCE.md).
        """
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
        if not rows:
            return []

        default = await self.default_schedule()
        profiles = (await self.ctx.session.execute(self.profiles.scoped_select())).scalars().all()
        by_user = {p.user_id: p for p in profiles}
        # A request may start before `date_from` and end after `date_to`; shape the whole of it.
        span_from = min(req.start_date for req, _ in rows)
        span_to = max(req.end_date for req, _ in rows)
        holidays_off = await self.active_holidays_between(span_from, span_to)

        return [
            TeamLeaveItem(
                id=req.id,
                user_id=req.user_id,
                user_name=user.full_name or user.email,
                leave_type_id=req.leave_type_id,
                start_date=req.start_date,
                start_time=req.start_time,
                end_date=req.end_date,
                end_time=req.end_time,
                hours=req.hours,
                status=LeaveRequestStatus(req.status),
                days=self._shaped_days(
                    req, self._effective(by_user.get(req.user_id), default)[0], holidays_off
                ),
            )
            for req, user in rows
        ]

    # --- dashboard widget ---------------------------------------------------------------- #
    async def summary(self) -> LeaveSummary:
        today = _now().date()
        year = today.year
        schedule, hours_week, _ = await self.profile_for(self.ctx.user.id)
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
            hours_per_week=hours_week,
            hours_per_day=sched.average_day_hours(schedule),
            pending_count=int(pending or 0),
            next_leave_start=next_leave.start_date if next_leave else None,
            next_leave_end=next_leave.end_date if next_leave else None,
        )
