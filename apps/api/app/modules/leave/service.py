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
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.core.auth.models import User
from app.core.events import emit
from app.core.models import Membership
from app.core.permissions.service import permission_holder_ids
from app.core.sorting import apply_sort, user_sort_name
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.leave import availability as avail
from app.modules.leave import holidays
from app.modules.leave import schedule as sched
from app.modules.leave.models import (
    AvailabilityChange,
    AvailabilityKind,
    EmploymentAvailability,
    EmploymentContract,
    EmploymentKind,
    LeaveCalendarDisplay,
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
    AvailabilityCreate,
    AvailabilityDay,
    AvailabilityMove,
    AvailabilityUpdate,
    AvailabilityWindow,
    EmploymentContractCreate,
    EmploymentContractUpdate,
    FreeTimeDay,
    FreeTimeOverview,
    HolidayImport,
    HolidayImportResult,
    LeaveBalance,
    LeaveDayHours,
    LeaveEntitlementUpsert,
    LeaveGroupBalance,
    LeaveHolidayCreate,
    LeaveHolidayUpdate,
    LeavePotBreakdown,
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
        # Statutory + extra present as one "Vakantieverlof" balance (#265): the two pots keep
        # their differing expiry (6 vs 60 months) so the legal split survives, but the employee
        # sees one number and a request spends the soonest-to-expire pot (statutory) first.
        "balance_group": "vacation",
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
        "balance_group": "vacation",
        "position": 20,
    },
    {
        # Free time / vrije tijd (#65, renamed #282): a movable free day drawn from the shortfall
        # between the employee's contract and the full-time norm. Auto-approve (the employee moves
        # it themselves), balance-tracked, no carry-over by default (tenant-editable — never
        # hardcoded law, §14). The key stays ``roostervrij`` (internal identifier — #282 kept it to
        # contain the rename); only the user-facing label became "Free time".
        "key": "roostervrij",
        "label_i18n": {"nl": "Vrije tijd", "en": "Free time"},
        "color": "cyan",
        "paid": True,
        "tracks_balance": True,
        "requires_approval": False,
        "accrues_schedule_gap": True,
        # Drawn as an hour block, not a full-day bar (#270): a free-time day is time off
        # *within* the normal schedule, and reading it as a week-of-vacation-shaped bar is what
        # this default corrects. Tenant-editable like every other property of a type.
        "calendar_display": LeaveCalendarDisplay.TIMED.value,
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

#: Combined labels for known balance groups (#265). Seed/label data like ``DEFAULT_LEAVE_TYPES``
#: itself (which hardcodes "Wettelijke vakantie" etc.), keyed by group slug — so a tenant renaming
#: the underlying types still reads one "Vakantieverlof" balance. The canonical UI copy is the web
#: message key ``leave.balance.vacation_group``; this is the API-side mirror non-web clients read.
#: An unknown tenant group falls back to its soonest-expiring type's own ``label_i18n``.
_GROUP_LABELS: dict[str, dict[str, str]] = {
    "vacation": {"nl": "Vakantieverlof", "en": "Vacation"},
}

#: A still-valid carried pot counts as "expiring soon" when it lapses within this many months of
#: org-local today — the "use it before it's gone" nudge on the combined balance (#265).
_EXPIRING_SOON_MONTHS = 6

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
        self.availability = ctx.repo(EmploymentAvailability)
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

    async def full_time_norm(self) -> Decimal:
        """The org's full-time week — the figure a contract's free time is the shortfall from.

        The org default schedule's own week (today 40 h), so a tenant whose full week is 36 h
        changes one setting rather than every contract. Named because three surfaces now quote
        it and none of them should re-derive it."""
        return sched.week_hours(await self.default_schedule())

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

    async def profile_for(
        self, user_id: uuid.UUID, on: date | None = None
    ) -> tuple[sched.WorkSchedule, Decimal, bool]:
        """``(effective schedule, contract hours, inherited)`` for one employee.

        ``on`` asks *which week applied on that date*: the working week lives on the employment
        contract, so a date covered by a contract carrying one resolves to it. Omit ``on`` for
        "the current arrangement" (settings screens, the dashboard widget), which is the same
        question asked of org-local **today** — the Dienstverband wizard writes the week onto
        the contract and nothing else, so a dateless read that skipped the contract lookup
        answered "40 h, 8 h/day" for anyone the wizard onboarded, while ``compute_hours`` and
        the preview priced the very same person's requests per contract. Two surfaces, two
        numbers, one request.
        """
        if on is None:
            on = await self._org_today()
        week = await self._contract_schedule_on(user_id, on)
        if week is not None:
            return week, sched.week_hours(week), False
        return self._effective(await self._profile(user_id), await self.default_schedule())

    async def employment_kind_on(
        self, user_id: uuid.UUID, on: date | None = None
    ) -> EmploymentKind | None:
        """The kind of the period covering ``on`` (org-local today by default), or ``None``.

        ``None`` is "no period on file for that day", which is not the same answer as
        ``EMPLOYEE`` — a screen that offers a freelancer's own controls must not offer them to
        everybody in a tenant that has never entered a contract. The one question the web asks
        to decide whether the availability surface exists at all.
        """
        if on is None:
            on = await self._org_today()
        for contract in await self._user_contracts(user_id):
            if contract.start_date <= on <= (contract.end_date or date.max):
                return EmploymentKind(contract.employment_type)
        return None

    async def _contract_schedule_on(
        self, user_id: uuid.UUID, day: date
    ) -> sched.WorkSchedule | None:
        """The week the contract covering ``day`` carries, or ``None`` to fall through.

        ``None`` covers both "no contract that day" and "a contract that records no week of its
        own" — the fallback chain is the same either way, so the caller needs no third case.
        """
        for contract in await self._user_contracts(user_id):
            if contract.start_date <= day <= (contract.end_date or date.max):
                return sched.parse(contract.schedule)
        return None

    @staticmethod
    def _resolver_from(
        periods: Sequence[tuple[date, date | None, sched.WorkSchedule | None]],
        fallback: sched.WorkSchedule,
    ) -> Callable[[date], sched.WorkSchedule]:
        """Build a ``date → week`` lookup over already-loaded contract periods.

        Split out from :meth:`schedule_resolver` so the team feed, which loads every contract in
        one query, resolves through exactly the same rule instead of a second copy of it.
        """

        def resolve(day: date) -> sched.WorkSchedule:
            for start, end, week in periods:
                if week is not None and start <= day <= (end or date.max):
                    return week
            return fallback

        return resolve

    async def schedule_resolver(
        self, user_id: uuid.UUID
    ) -> Callable[[date], sched.WorkSchedule]:
        """A ``date → week`` resolver built from **one** contract read and one profile read.

        Pricing a span walks it day by day, and a span can cross a contract boundary (someone who
        went from four days to five on 1 March). Resolving per day through ``profile_for`` would be
        two queries per day; this pays for the lookup once and answers from memory
        (docs/PERFORMANCE.md).
        """
        periods = [
            (c.start_date, c.end_date, sched.parse(c.schedule))
            for c in await self._user_contracts(user_id)
        ]
        fallback, _, _ = self._effective(
            await self._profile(user_id), await self.default_schedule()
        )
        return self._resolver_from(periods, fallback)

    async def effective_schedule(
        self, user_id: uuid.UUID, on: date | None = None
    ) -> sched.WorkSchedule:
        """The week ``compute_hours`` measures against: the contract covering ``on``, else this
        employee's own, else the org's."""
        schedule, _, _ = await self.profile_for(user_id, on)
        return schedule

    async def hours_per_week(self, user_id: uuid.UUID) -> Decimal:
        _, hours, _ = await self.profile_for(user_id)
        return hours

    async def list_profiles(self) -> list[tuple[uuid.UUID, Decimal, sched.WorkSchedule | None]]:
        """Every employee's current arrangement for the managers' roster
        (``leave.profile.manage``).

        Returns each employee's **own** schedule (``None`` = follows the org default) rather
        than the merged one: the settings screen already holds the default and rendering
        "inherited" is the whole point of the distinction. "Own" is answered the way
        :meth:`profile_for` answers it — the contract in force today first, the profile as the
        legacy fallback — and covers members who have a contract but no profile row at all,
        which is everyone the Dienstverband wizard onboarded. Two queries for the whole
        roster, never one per member (docs/PERFORMANCE.md).
        """
        self.ctx.require("leave.profile.manage")
        rows = (await self.ctx.session.execute(self.profiles.scoped_select())).scalars().all()
        by_user: dict[uuid.UUID, tuple[Decimal, sched.WorkSchedule | None]] = {
            p.user_id: (p.hours_per_week, sched.parse(p.schedule)) for p in rows
        }
        today = await self._org_today()
        contract_rows = (
            (await self.ctx.session.execute(self.contracts.scoped_select())).scalars().all()
        )
        for contract in contract_rows:
            if not contract.start_date <= today <= (contract.end_date or date.max):
                continue
            week = sched.parse(contract.schedule)
            if week is None:
                continue
            by_user[contract.user_id] = (sched.week_hours(week), week)
        return [(uid, hours, week) for uid, (hours, week) in by_user.items()]

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
            # The week now lives on the contract, so writing only the profile would save into a
            # field the resolver never reaches for anyone who has one: "save" would silently do
            # nothing. Saving *the current week* means saving it onto the arrangement in force,
            # so it lands on every contract still running. Ended contracts keep their own week —
            # that history is the whole point of moving it here.
            await self._apply_schedule_to_current_contracts(user_id, values["schedule"])
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

    async def _apply_schedule_to_current_contracts(
        self, user_id: uuid.UUID, schedule: dict | None
    ) -> None:
        """Push a saved week onto every contract that has not ended yet.

        "Not ended" is ``end_date`` in the future or absent — a contract that ran out yesterday
        describes a period that is over, and rewriting its week would rewrite history. A future
        contract is included: it has not started, so it has no history to protect and the week
        just saved is the best answer anyone has for it.
        """
        today = await self._org_today()
        touched = False
        for contract in await self._user_contracts(user_id):
            if contract.end_date is not None and contract.end_date < today:
                continue
            await self.contracts.update(contract, schedule=schedule)
            touched = True
        if touched:
            # The same rule as update_contract: the week feeds the entitlement (the half-day
            # rounding, and the contract-less legacy fallback), so a rewritten contract week
            # re-derives the generated pots — this path must not be the one door that skips
            # #264 while the contract endpoint honours it for the very same field.
            await self._recompute_generated_entitlements(user_id)

    async def _member_or_404(self, user_id: uuid.UUID) -> None:
        membership = await self.ctx.session.scalar(
            select(Membership).where(
                Membership.org_id == self.ctx.org.id, Membership.user_id == user_id
            )
        )
        if membership is None:
            raise AppError("not_found", "errors.not_found", status_code=404)

    # --- hourly rate (#82, #113) -------------------------------------------------- #
    async def default_rate(self) -> Decimal | None:
        """The org's house rate (#113); ``None`` when the tenant never set one."""
        row = await self.settings_row()
        return row.default_hourly_rate if row else None

    async def effective_rate(self, own_rate: Decimal | None) -> Decimal | None:
        """#113's one fallback chain: employee rate (#82) → org default → ``None``.

        The single resolver every consumer reads — the rate endpoints here, the cost math of
        #111 — so nobody re-implements the fallback. Explicit ``is None``: a recorded rate of
        0 means "costs nothing", never "fall back to the default".
        """
        return own_rate if own_rate is not None else await self.default_rate()

    async def get_rate(
        self, user_id: uuid.UUID | None = None
    ) -> tuple[uuid.UUID, Decimal | None, Decimal | None]:
        """One employee's ``(id, own rate, effective rate)``. Own on ``leave.rate.read:own``;
        another's needs ``:any``.

        A rate is salary-adjacent, so this never leaks across employees: a plain member reading
        someone else is refused before any row is loaded.
        """
        uid = user_id or self.ctx.user.id
        scope = None if uid == self.ctx.user.id else "any"
        self.ctx.require("leave.rate.read", scope=scope)
        if uid != self.ctx.user.id:
            await self._member_or_404(uid)
        profile = await self._profile(uid)
        own = profile.hourly_rate if profile else None
        return uid, own, await self.effective_rate(own)

    async def list_rates(self) -> list[tuple[uuid.UUID, Decimal | None, Decimal | None]]:
        """Every employee's ``(id, own, effective)`` for the managers' roster (``:any`` only)."""
        self.ctx.require("leave.rate.read", scope="any")
        rows = (await self.ctx.session.execute(self.profiles.scoped_select())).scalars().all()
        default = await self.default_rate()
        return [
            (p.user_id, p.hourly_rate, p.hourly_rate if p.hourly_rate is not None else default)
            for p in rows
        ]

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
        """The rostered hours of this contract's own week: its ``schedule`` when it carries one,
        else the employee's profile schedule (legacy), else the org default.

        Since #282 this feeds no balance — free time accrues from the norm shortfall, not from
        ``scheduled − contract`` — so it is the number the contract list renders and the figure
        the wizard reconciles against the contract hours."""
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
        self._validate_contract_hours(data.employment_type, data.contract_hours_per_week)
        await self._ensure_no_contract_overlap(
            data.user_id, data.start_date, data.end_date, exclude_id=None
        )
        values = data.model_dump(exclude={"schedule"})
        values["schedule"] = sched.dump(data.schedule) if data.schedule else None
        contract = await self.contracts.create(**values)
        # #105: the contract and the entitlements it earns commit atomically — no separate,
        # easily-forgotten "Genereer" step before the new hire has a balance. A new contract
        # always changes coverage (a raise's second period folds into the year), so always
        # recompute (#264).
        await self._recompute_generated_entitlements(contract.user_id)
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
        # Both fields resolved against the row as it *will* be: switching a period to freelance
        # and clearing its hours is one edit, and so is the reverse — reading either from the
        # stored row would refuse the half of it that is being fixed.
        self._validate_contract_hours(
            values.get("employment_type", contract.employment_type),
            values.get("contract_hours_per_week", contract.contract_hours_per_week),
        )
        await self._ensure_no_contract_overlap(
            contract.user_id, start, end, exclude_id=contract.id
        )
        updated = await self.contracts.update(contract, **values)
        # A corrected/terminated period re-derives the generated pots for the current + future
        # years it touches (#264): a shorter span prorates down, a raise folds in. Only when a
        # field that actually feeds the entitlement moved — a note-only edit changes no balance,
        # so it shouldn't churn the rows.
        if data.model_fields_set & {
            "start_date",
            "end_date",
            "employment_type",
            "contract_hours_per_week",
            "free_time_hours_per_week",
            "schedule",
        }:
            await self._recompute_generated_entitlements(updated.user_id)
        return updated

    async def delete_contract(self, contract_id: uuid.UUID) -> None:
        self.ctx.require("leave.profile.manage")
        contract = await self.contracts.get_or_404(contract_id)
        user_id = contract.user_id
        await self.contracts.delete(contract)
        # A removed contract's auto pots are now derived from a period that no longer exists (#264).
        await self._recompute_generated_entitlements(user_id)

    def _validate_contract_hours(
        self, kind: EmploymentKind | str, hours: Decimal | None
    ) -> None:
        """Only a freelance period may record no agreed hours.

        That asymmetry is what keeps the accrual paths total: statutory vacation is
        ``weeks × contract hours``, so an employee period without them would prorate against a
        number nobody entered. A freelancer engaged per assignment genuinely has none, and an
        invented 16 there is worse than a blank — every capacity figure would quote it as if
        somebody had agreed to it.
        """
        if hours is None and kind != EmploymentKind.FREELANCE:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"contract_hours_per_week": "errors.leave_contract_hours_required"},
            )

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
    def _accruing(contracts: Sequence[EmploymentContract]) -> list[EmploymentContract]:
        """The periods that earn leave — the payroll ones.

        A freelance engagement accrues no statutory vacation and no free time: there is no
        entitlement to prorate, and deriving one would hand a ZZP'er a balance that goes negative
        the first week they take off. Filtered here rather than at each of the two computations,
        so "which periods earn" has one answer (see :class:`EmploymentKind`).
        """
        return [c for c in contracts if c.employment_type != EmploymentKind.FREELANCE]

    @staticmethod
    def _contract_free_time(contract: EmploymentContract, full_time_norm: Decimal) -> Decimal:
        """Free time (vrije tijd) this contract accrues per week.

        ``free_time_hours_per_week`` when the tenant recorded one, else the #282 rule
        ``max(0, norm − contract hours)``. The stored value wins even when it is ``0``: "the free
        time is already in this person's roster" is a deliberate answer, and it is the whole
        reason the column exists — deriving it would hand a 32-h part-timer who already has
        Friday off a second pot of ~52 days (see the model docstring).

        A freelance period accrues none of it, and that check comes **first**: it is what the
        contract list renders, and such a period may carry no agreed hours at all — there would
        be nothing to subtract from the norm.
        """
        if contract.employment_type == EmploymentKind.FREELANCE:
            return Decimal(0)
        if contract.free_time_hours_per_week is not None:
            return max(Decimal(0), contract.free_time_hours_per_week)
        return max(Decimal(0), full_time_norm - (contract.contract_hours_per_week or Decimal(0)))

    @staticmethod
    def _round_half_day(hours: Decimal, avg_day_hours: Decimal) -> Decimal:
        """Free time accrues in awkward totals (104,3 h); round to the nearest bookable half day."""
        half = avg_day_hours / 2
        if half <= 0:
            return hours.quantize(Decimal("0.01"))
        steps = (hours / half).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return (steps * half).quantize(Decimal("0.01"))

    # --- availability (freelance) --------------------------------------------------- #
    def _availability_user(self, user_id: uuid.UUID | None, *, write: bool) -> uuid.UUID:
        """Whose availability to act on. Your own always; anyone else's needs ``:any``.

        A freelancer keeping their own calendar is the ordinary case here, which is why the
        member default is ``:own`` rather than nothing at all — a screen every freelancer needs
        and only an admin can open does not read as a policy, it reads as a broken screen (#310).
        """
        key = "leave.availability.write" if write else "leave.availability.read"
        if user_id is None or user_id == self.ctx.user.id:
            self.ctx.require(key)
            return self.ctx.user.id
        self.ctx.require(key, scope="any")
        return user_id

    async def _availability_or_404(self, entry_id: uuid.UUID) -> EmploymentAvailability:
        """404, not 403, on someone else's row — the §15 rule: a 403 confirms it exists."""
        row = await self.availability.get_or_404(entry_id)
        if row.user_id != self.ctx.user.id and not self.ctx.can(
            "leave.availability.read", scope="any"
        ):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def list_availability(
        self,
        *,
        date_from: date,
        date_to: date,
        user_id: uuid.UUID | None = None,
        all_users: bool = False,
    ) -> Sequence[EmploymentAvailability]:
        """The exception rows with an occurrence inside the window.

        Narrowed to the caller's own unless they hold ``:any`` — a row carries a note and is the
        person's own record of their week, unlike the computed day view below, which is roster
        information the whole team may read.

        The window filter is applied in Python rather than in SQL: a repeat's occurrences are a
        cadence, not a column, so "does any land in June" is not a range predicate. The read is
        bounded by ``user_id`` (or the org's staff) and by the fact that these are exceptions —
        somebody with hundreds of them has a different problem.
        """
        stmt = self.availability.scoped_select().order_by(
            EmploymentAvailability.date.asc(), EmploymentAvailability.created_at.asc()
        )
        if all_users:
            self.ctx.require("leave.availability.read", scope="any")
        else:
            stmt = stmt.where(
                EmploymentAvailability.user_id
                == self._availability_user(user_id, write=False)
            )
        rows = (await self.ctx.session.execute(stmt)).scalars().all()
        return [r for r in rows if avail.overlaps_window(r, date_from, date_to)]

    async def availability_days(
        self,
        *,
        date_from: date,
        date_to: date,
        user_id: uuid.UUID | None = None,
        all_users: bool = False,
    ) -> list[AvailabilityDay]:
        """The resolved week: each person's each day, base schedule bent by their exceptions.

        Team-visible for staff, like the absence feed (``team``): who is available is ordinary
        team information in an agency, and a ``client`` role holds none of these permissions at
        all. Notes stay behind ``list_availability``.

        Four reads for the whole window, never one per day or per person (docs/PERFORMANCE.md):
        the contracts, the profiles, the org default and the exception rows. Holidays are
        deliberately *not* applied — a freelancer has no employer's holiday calendar, and one who
        is not working Koningsdag says so with an ``unavailable`` row like any other day.
        """
        if date_to < date_from:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"date_to": "errors.leave_end_before_start"},
            )
        self.ctx.require("leave.availability.read")
        target = None if all_users else self._availability_user(user_id, write=False)

        stmt = self.availability.scoped_select()
        if target is not None:
            stmt = stmt.where(EmploymentAvailability.user_id == target)
        rows = (
            (
                await self.ctx.session.execute(
                    stmt.order_by(
                        EmploymentAvailability.date.asc(),
                        EmploymentAvailability.created_at.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_user: dict[uuid.UUID, list[EmploymentAvailability]] = {}
        for row in rows:
            if avail.overlaps_window(row, date_from, date_to):
                by_user.setdefault(row.user_id, []).append(row)
        # Someone asked about *this* person: answer for them even with no exceptions on file,
        # because "the base week, unbent" is a real answer and an empty list is not.
        if target is not None:
            by_user.setdefault(target, [])

        default = await self.default_schedule()
        profiles = (await self.ctx.session.execute(self.profiles.scoped_select())).scalars().all()
        profiles_by_user = {p.user_id: p for p in profiles}
        contracts_by_user: dict[uuid.UUID, list[EmploymentContract]] = {}
        for contract in (
            (await self.ctx.session.execute(self.contracts.scoped_select())).scalars().all()
        ):
            contracts_by_user.setdefault(contract.user_id, []).append(contract)
        # Names for the people actually answered for, and the zone every instant below is
        # anchored in — one query each for the whole window, never one per day (PERFORMANCE.md).
        names: dict[uuid.UUID, str] = {}
        if by_user:
            names = dict(
                (
                    await self.ctx.session.execute(
                        select(User.id, func.coalesce(User.full_name, User.email)).where(
                            User.id.in_(list(by_user))
                        )
                    )
                ).all()
            )
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)

        days: list[AvailabilityDay] = []
        for uid, entries in by_user.items():
            resolver = self._resolver_from(
                [
                    (c.start_date, c.end_date, sched.parse(c.schedule))
                    for c in contracts_by_user.get(uid, ())
                ],
                self._effective(profiles_by_user.get(uid), default)[0],
            )
            day = date_from
            while day <= date_to:
                applicable = [e for e in entries if avail.occurs_on(e, day)]
                week = resolver(day)
                base = week.day(day.weekday())
                intervals = avail.resolve_day(
                    base, avail.typical_day(week, default), applicable
                )
                base_hours = avail.hours(avail.base_intervals(base))
                hours = avail.hours(intervals)
                # The hull, not each stretch: an ordinary day is two windows either side of
                # lunch, and a grid block from 08:30 to 17:00 is what that day means (#270).
                starts_at = ends_at = None
                if intervals:
                    starts_at = datetime.combine(
                        day, avail.as_clock(intervals[0][0])
                    ).replace(tzinfo=zone)
                    ends_at = datetime.combine(day, avail.as_clock(intervals[-1][1])).replace(
                        tzinfo=zone
                    )
                days.append(
                    AvailabilityDay(
                        user_id=uid,
                        user_name=names.get(uid, ""),
                        date=day,
                        windows=[
                            AvailabilityWindow(
                                start=avail.as_clock(start), end=avail.as_clock(end)
                            )
                            for start, end in intervals
                        ],
                        hours=hours,
                        base_hours=base_hours,
                        change=self._availability_change(base_hours, hours),
                        starts_at=starts_at,
                        ends_at=ends_at,
                        deviates=bool(applicable),
                        entry_ids=[e.id for e in applicable],
                    )
                )
                day += timedelta(days=1)
        days.sort(key=lambda d: (d.date, str(d.user_id)))
        return days

    @staticmethod
    def _availability_change(base: Decimal, resolved: Decimal) -> AvailabilityChange | None:
        """Which way the day moved. ``None`` when it did not — see :class:`AvailabilityChange`."""
        if resolved == base:
            return None
        if base <= 0:
            return AvailabilityChange.ADDED
        if resolved <= 0:
            return AvailabilityChange.REMOVED
        return AvailabilityChange.CHANGED

    async def create_availability(self, data: AvailabilityCreate) -> EmploymentAvailability:
        user_id = self._availability_user(data.user_id, write=True)
        await self._member_or_404(user_id)
        values = data.model_dump(exclude={"user_id"})
        # ``Clock``'s serializer stringifies in model_dump() too, and asyncpg refuses a string
        # for a TIME column — the ORM needs the actual time objects (same as create_recurring).
        values["start_time"] = data.start_time
        values["end_time"] = data.end_time
        row = await self.availability.create(user_id=user_id, **values)
        await self._emit_availability("availability.saved", row)
        return row

    async def update_availability(
        self, entry_id: uuid.UUID, data: AvailabilityUpdate
    ) -> EmploymentAvailability:
        row = await self._availability_or_404(entry_id)
        self._availability_user(row.user_id, write=True)
        values = data.model_dump(exclude_unset=True)
        # model_dump() stringifies Clock fields; the TIME columns need the time objects.
        for field in ("start_time", "end_time"):
            if field in values:
                values[field] = getattr(data, field)
        # Re-run the create-time rules against the row as it *will* be: a PATCH that only moves
        # ``repeat_until`` must not be able to leave a bound behind on a row whose cadence it
        # just cleared, and every rule here is one the POST already refuses.
        self._validate_availability(
            day=values.get("date", row.date),
            start_time=values.get("start_time", row.start_time),
            end_time=values.get("end_time", row.end_time),
            repeat_weeks=values.get("repeat_weeks", row.repeat_weeks),
            repeat_until=values.get("repeat_until", row.repeat_until),
        )
        updated = await self.availability.update(row, **values)
        await self._emit_availability("availability.saved", updated)
        return updated

    async def delete_availability(self, entry_id: uuid.UUID) -> None:
        """Delete a row — and the other half, when it is a move.

        Half a move is a statement nobody made: dropping only the ``unavailable`` leaves an extra
        Thursday whose reason has vanished, and dropping only the ``extra`` leaves someone
        unavailable on the Tuesday they had already agreed to swap.
        """
        row = await self._availability_or_404(entry_id)
        self._availability_user(row.user_id, write=True)
        rows = [row]
        if row.pair_id is not None:
            rows = list(
                (
                    await self.ctx.session.execute(
                        self.availability.scoped_select().where(
                            EmploymentAvailability.pair_id == row.pair_id
                        )
                    )
                )
                .scalars()
                .all()
            ) or [row]
        for entry in rows:
            # Emitted *before* the delete: the handler snapshots what it needs off the row, and
            # a flushed-away row has nothing left to read.
            await self._emit_availability("availability.gone", entry)
            await self.availability.delete(entry)

    async def move_availability(
        self, data: AvailabilityMove
    ) -> list[EmploymentAvailability]:
        """"Not Tuesday, Thursday instead" — written as the two rows it is, sharing a ``pair_id``.

        The pair is what lets the UI render one line and undo one act; the two rows are what
        lets the day view resolve two different days without knowing anything about moves.
        """
        user_id = self._availability_user(data.user_id, write=True)
        await self._member_or_404(user_id)
        self._validate_availability(
            day=data.to_date,
            start_time=data.start_time,
            end_time=data.end_time,
            repeat_weeks=data.repeat_weeks,
            repeat_until=data.repeat_until,
        )
        pair_id = uuid.uuid4()
        dropped = await self.availability.create(
            user_id=user_id,
            kind=AvailabilityKind.UNAVAILABLE.value,
            date=data.from_date,
            repeat_weeks=data.repeat_weeks,
            repeat_until=data.repeat_until,
            pair_id=pair_id,
            note=data.note,
        )
        added = await self.availability.create(
            user_id=user_id,
            kind=AvailabilityKind.EXTRA.value,
            date=data.to_date,
            start_time=data.start_time,
            end_time=data.end_time,
            repeat_weeks=data.repeat_weeks,
            repeat_until=data.repeat_until,
            pair_id=pair_id,
            note=data.note,
        )
        for row in (dropped, added):
            await self._emit_availability("availability.saved", row)
        return [dropped, added]

    async def _emit_availability(self, event: str, row: EmploymentAvailability) -> None:
        """Announce an availability row to the bus, for whoever mirrors it (#6's rule: the owning
        module emits, interested modules subscribe — leave imports no Google internals).

        The payload is the **row**, not the day it resolves to. A resolved day is the base week
        bent by exceptions, and a mirror has no base week to bend: pushing the resolution would
        mean pushing every ordinary working day too. One row ↔ one mirrored event is also what
        makes an edit an update and a delete a delete, rather than a diff nobody can compute.
        """
        await emit(
            event,
            self.ctx,
            {
                "user_id": str(row.user_id),
                "availability_id": str(row.id),
                "kind": row.kind,
                "date": row.date.isoformat(),
                "start_time": row.start_time.isoformat() if row.start_time else None,
                "end_time": row.end_time.isoformat() if row.end_time else None,
                "repeat_weeks": row.repeat_weeks,
                "repeat_until": row.repeat_until.isoformat() if row.repeat_until else None,
                "note": row.note,
            },
        )

    @staticmethod
    def _validate_availability(
        *,
        day: date,
        start_time: time | None,
        end_time: time | None,
        repeat_weeks: int | None,
        repeat_until: date | None,
    ) -> None:
        """The schema's own rules, re-stated for a PATCH that only sends half of them."""
        if start_time is not None and end_time is not None and start_time >= end_time:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"end_time": "errors.leave_availability_window_invalid"},
            )
        if repeat_until is not None:
            if repeat_weeks is None:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"repeat_until": "errors.leave_availability_repeat_required"},
                )
            if repeat_until < day:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"repeat_until": "errors.leave_availability_until_invalid"},
                )

    # --- recurring free days / free time (#107) ------------------------------------ #
    async def list_recurring(
        self, *, user_id: uuid.UUID | None = None
    ) -> list[tuple[LeaveRecurringDay, int]]:
        """Patterns + how many days each still has standing ahead of today.

        A member sees their own; ``leave.profile.manage`` sees anyone's/all. The count travels
        with the row because deleting a pattern is a decision about *those days*: the pattern is
        a rule, the days it laid down are real leave someone planned around, and "delete" has to
        say how many are at stake rather than leaving them stranded on the calendar. One grouped
        query for the whole list, never one per pattern (docs/PERFORMANCE.md).
        """
        if not self.ctx.can("leave.profile.manage"):
            if user_id is not None and user_id != self.ctx.user.id:
                raise AppError("forbidden", "errors.forbidden", status_code=403)
            user_id = self.ctx.user.id
        stmt = self.recurring.scoped_select().order_by(
            LeaveRecurringDay.user_id, LeaveRecurringDay.anchor_date
        )
        if user_id is not None:
            stmt = stmt.where(LeaveRecurringDay.user_id == user_id)
        patterns = list((await self.ctx.session.execute(stmt)).scalars().all())
        if not patterns:
            return []

        today = await self._org_today()
        counts = dict(
            (
                await self.ctx.session.execute(
                    select(LeaveRequest.recurring_day_id, func.count())
                    .where(
                        LeaveRequest.org_id == self.ctx.org.id,
                        LeaveRequest.recurring_day_id.in_([p.id for p in patterns]),
                        LeaveRequest.status.in_(_OCCUPYING),
                        LeaveRequest.start_date >= today,
                    )
                    .group_by(LeaveRequest.recurring_day_id)
                )
            ).all()
        )
        return [(p, counts.get(p.id, 0)) for p in patterns]

    async def _upcoming_pattern_days(self, pattern_id: uuid.UUID) -> list[LeaveRequest]:
        """This pattern's still-standing days from today on, soonest first.

        Today's own day counts as upcoming: it has not been worked yet, and a manager removing a
        pattern this morning means today too.
        """
        today = await self._org_today()
        return list(
            (
                await self.ctx.session.execute(
                    self.requests.scoped_select()
                    .where(
                        LeaveRequest.recurring_day_id == pattern_id,
                        LeaveRequest.status.in_(_OCCUPYING),
                        LeaveRequest.start_date >= today,
                    )
                    .order_by(LeaveRequest.start_date.asc())
                )
            )
            .scalars()
            .all()
        )

    def _ensure_may_manage_pattern(self, user_id: uuid.UUID, leave_type: LeaveType) -> None:
        """Who may shape a pattern, and for which type.

        ``leave.profile.manage`` may plan any active type for anyone — their act is the
        approval, as everywhere. An employee may plan their **own** pattern, but only for a
        **balance-tracked self-service type** (``requires_approval = false`` and
        ``tracks_balance``, i.e. free time): generated days are auto-approved, so an
        approval-requiring type would be a batch bypass of the approval flow — and a
        trackless one ("sick") has no pot to bound it, besides a *recurring sick day* not
        being a thing anyone plans. The balance cap bounds what self-service can grant.
        """
        if self.ctx.can("leave.profile.manage"):
            return
        if user_id != self.ctx.user.id:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        self.ctx.require("leave.request.write")
        if leave_type.requires_approval or not leave_type.tracks_balance:
            raise AppError(
                "forbidden", "errors.leave_recurring_needs_manager", status_code=403
            )

    async def _owned_recurring_or_404(self, recurring_id: uuid.UUID) -> LeaveRecurringDay:
        """404, not 403, on someone else's pattern — a 403 would confirm that it exists."""
        pattern = await self.recurring.get_or_404(recurring_id)
        if pattern.user_id != self.ctx.user.id and not self.ctx.can("leave.profile.manage"):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return pattern

    async def create_recurring(
        self, data: LeaveRecurringDayCreate
    ) -> tuple[LeaveRecurringDay, int]:
        """Define a pattern and immediately lay its days onto the calendar."""
        await self._member_or_404(data.user_id)
        leave_type = await self.types.get_or_404(data.leave_type_id)
        if not leave_type.active:
            raise AppError("validation", "errors.validation", status_code=422)
        self._ensure_may_manage_pattern(data.user_id, leave_type)
        # Same same-day rule as a request: an inverted part-day window is a typo, not a wish.
        self._validate_span(data.anchor_date, data.start_time, data.anchor_date, data.end_time)
        values = data.model_dump()
        # ``Clock``'s serializer stringifies in model_dump() too, and asyncpg refuses a string
        # for a TIME column — the ORM needs the actual time objects.
        values["start_time"] = data.start_time
        values["end_time"] = data.end_time
        if data.days_per_year:
            # The nearest equivalent cadence, so a rolled-back image (which reads only
            # ``interval_weeks``) keeps a comparable rhythm — see the migration.
            values["interval_weeks"] = self._interval_for(data.days_per_year)
        pattern = await self.recurring.create(**values)
        created = await self.generate_recurring_days(pattern_id=pattern.id)
        return pattern, created

    async def update_recurring(
        self, recurring_id: uuid.UUID, data: LeaveRecurringDayUpdate
    ) -> tuple[LeaveRecurringDay, int]:
        pattern = await self._owned_recurring_or_404(recurring_id)
        values = data.model_dump(exclude_unset=True)
        # model_dump() stringifies Clock fields; the TIME columns need the time objects.
        if "start_time" in values:
            values["start_time"] = data.start_time
        if "end_time" in values:
            values["end_time"] = data.end_time
        if values.get("days_per_year"):
            values["interval_weeks"] = self._interval_for(values["days_per_year"])
        leave_type = await self.types.get_or_404(
            values.get("leave_type_id", pattern.leave_type_id)
        )
        if "leave_type_id" in values and not leave_type.active:
            raise AppError("validation", "errors.validation", status_code=422)
        self._ensure_may_manage_pattern(pattern.user_id, leave_type)
        self._validate_span(
            values.get("anchor_date", pattern.anchor_date),
            values.get("start_time", pattern.start_time),
            values.get("anchor_date", pattern.anchor_date),
            values.get("end_time", pattern.end_time),
        )
        pattern = await self.recurring.update(pattern, **values)
        created = 0
        if pattern.active:
            created = await self.generate_recurring_days(pattern_id=pattern.id)
        return pattern, created

    async def delete_recurring(
        self, recurring_id: uuid.UUID, *, withdraw_days: bool = False
    ) -> int:
        """Delete the pattern, and optionally take back the days it placed. Returns how many.

        By default the days stay (the FK is ``SET NULL``): they are real, individually cancellable
        leave the employee planned around, and a rule being deleted is not a reason to wipe
        someone's calendar. But leaving the caller *no* way to clear them made "delete" a job half
        done — a year of free Fridays with nothing left pointing at them and no bulk way out. So
        the choice is offered on the delete itself, and answered explicitly by whoever is deleting.

        ``withdraw_days`` cancels the still-standing days from today on, through the ordinary
        :meth:`cancel` path, so the past stays locked, the permission rules hold and the Google
        mirror is told. The past is never touched: those days were taken. A day the caller may not
        cancel is skipped rather than aborting the delete — the pattern still goes.
        """
        pattern = await self._owned_recurring_or_404(recurring_id)
        leave_type = await self.types.get_or_404(pattern.leave_type_id)
        self._ensure_may_manage_pattern(pattern.user_id, leave_type)
        withdrawn = 0
        if withdraw_days:
            for request in await self._upcoming_pattern_days(pattern.id):
                try:
                    await self.cancel(request.id)
                except AppError:
                    continue
                withdrawn += 1
        await self.recurring.delete(pattern)
        return withdrawn

    @staticmethod
    def _add_months(day: date, months: int) -> date:
        month = day.month - 1 + months
        year = day.year + month // 12
        month = month % 12 + 1
        last_day = monthrange(year, month)[1]
        return date(year, month, min(day.day, last_day))

    @staticmethod
    def _weekday_dates(weekday: int, start: date, end: date) -> list[date]:
        """Every ``weekday`` in ``[start, end]``, in order (empty when the range holds none)."""
        day = start + timedelta(days=(weekday - start.weekday()) % 7)
        out: list[date] = []
        while day <= end:
            out.append(day)
            day += timedelta(weeks=1)
        return out

    @staticmethod
    def _round_half_up(value: Decimal) -> int:
        """Round half **up**, not to even. ``round()`` turns 2,5 days into 2, which reads as an
        off-by-one to everyone who did the arithmetic themselves."""
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @classmethod
    def _interval_for(cls, days_per_year: int) -> int:
        """The cadence a spread pattern most nearly is, clamped to the schema's 1..8 weeks.

        Written alongside ``days_per_year`` purely so a **rolled-back** image — which knows only
        ``interval_weeks`` — keeps a comparable rhythm instead of falling to the column default of
        every week (see the migration).
        """
        if days_per_year <= 0:
            return 1
        return max(1, min(8, cls._round_half_up(Decimal(52) / Decimal(days_per_year))))

    def _occurrence_plan(
        self, pattern: LeaveRecurringDay, today: date, horizon: date
    ) -> list[tuple[date, int | None]]:
        """The dates to attempt, each with the running per-year cap that applies after it.

        Two shapes, because two different things are known (see the model docstring):

        * **Interval** (``days_per_year`` is ``NULL``) — every ``interval_weeks`` from the anchor.
          The cap is ``None``: the cadence *is* the answer, so every occurrence is attempted.
        * **Spread** — every candidate weekday is offered, but a candidate may only be used while
          the year's placed count is still under ``ceil((i + 1) × target / total)``. That is what
          makes the days come out evenly *and* self-correcting: a candidate skipped for a holiday
          consumes no quota, so the next week immediately becomes eligible and the year still ends
          up with the number of days the pot bought. A fixed cadence cannot recover a lost day.

          The quota universe runs from the **anchor**, never from today: prorating against the
          window still ahead re-derives a fresh surplus on every run (the placed days that
          front-loaded the quota fall out of both the candidate list and the placed count), so
          the monthly cron would quietly hand out extra days forever. Past candidates therefore
          *do* appear in the plan — the generator counts the spent ones against the cap and
          never places the rest, so a re-run at any later date reproduces the original
          arithmetic and adds nothing.

        Neither shape backfills: interval mode starts at/after today, and spread mode's past
        candidates exist for bookkeeping only — the generator skips placing them.
        """
        weekday = pattern.anchor_date.weekday()
        if pattern.days_per_year is None:
            step = timedelta(weeks=pattern.interval_weeks)
            occurrence = pattern.anchor_date
            if occurrence < today:
                behind = (today - occurrence).days
                occurrence += -(-behind // (pattern.interval_weeks * 7)) * step  # ceil
            plan: list[tuple[date, int | None]] = []
            while occurrence <= horizon:
                plan.append((occurrence, None))
                occurrence += step
            return plan

        plan = []
        for year in range(pattern.anchor_date.year, horizon.year + 1):
            candidates = self._weekday_dates(
                weekday,
                max(pattern.anchor_date, date(year, 1, 1)),
                min(horizon, date(year, 12, 31)),
            )
            if not candidates:
                continue
            # Prorate to the part of the year actually in range: a pattern started in July buys
            # half the days, which is also what the entitlement prorated to.
            full_year = len(
                self._weekday_dates(weekday, date(year, 1, 1), date(year, 12, 31))
            )
            target = self._round_half_up(
                Decimal(pattern.days_per_year) * Decimal(len(candidates)) / Decimal(full_year)
            )
            if target <= 0:
                continue
            total = len(candidates)
            for index, day in enumerate(candidates):
                # Ceiling, so the **first** free day is the anchor the user picked rather than
                # one cadence later.
                cap = -(-(index + 1) * target // total)
                plan.append((day, cap))
        return plan

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
        contract earned (§14). A skipped-for-balance occurrence is retried on
        the next run, so a far-out day materializes the moment its year's pot is seeded.
        Runs from pattern saves and the monthly cron.

        Which dates are attempted is :meth:`_occurrence_plan`'s business — a fixed cadence, or a
        year's worth of days spread evenly. Everything below is identical either way: the plan
        only decides *when* to try, never whether a day may be taken.
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
            # One `date → week` lookup for the whole pattern: this loop prices a year of
            # occurrences, and rebuilding the resolver per day would be two queries a day
            # (docs/PERFORMANCE.md).
            schedule_on = await self.schedule_resolver(pattern.user_id)
            plan = self._occurrence_plan(pattern, today, horizon)

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
            placed_by_year: dict[int, int] = {}

            for day, cap in plan:
                if day in spent:
                    # A spent occurrence still counts against the year's cap. Without this, a
                    # second run would read the quota as unfilled and place the same days again.
                    placed_by_year[day.year] = placed_by_year.get(day.year, 0) + 1
                    continue
                if cap is not None and placed_by_year.get(day.year, 0) >= cap:
                    continue  # spread mode: this year is far enough ahead already
                if day < today:
                    # An unspent past candidate is quota bookkeeping only (spread mode's
                    # universe runs from the anchor) — a missed day is never backfilled.
                    continue
                hours, _ = await self.compute_hours(
                    pattern.user_id,
                    day,
                    pattern.start_time,
                    day,
                    pattern.end_time,
                    resolver=schedule_on,
                )
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
                # The display window resolves against *this* occurrence's schedule: the week
                # can change at a contract boundary inside the horizon, and the hours above
                # are already priced per day. The resolver answers from memory, so this costs
                # no queries; a whole-day pattern short-circuits to (None, None).
                resolved_start, resolved_end = self._resolve_bounds(
                    schedule_on(day), day, pattern.start_time, day, pattern.end_time
                )
                # An auto-approved registration, like a sick report: defining the pattern was
                # the sanctioned act (a manager's, or the owner's own self-service type), and
                # the employee moves individual days within the rules.
                request = await self.requests.create(
                    user_id=pattern.user_id,
                    leave_type_id=pattern.leave_type_id,
                    start_date=day,
                    start_time=pattern.start_time,
                    end_date=day,
                    end_time=pattern.end_time,
                    resolved_start_time=resolved_start,
                    resolved_end_time=resolved_end,
                    hours=hours,
                    note=pattern.note,
                    status=LeaveRequestStatus.APPROVED.value,
                    recurring_day_id=pattern.id,
                    recurring_date=day,
                )
                # Born approved: the mirror hears about it now or never — cancel already told
                # it (§14), but a *placed* day never did, so a fresh pattern's days existed
                # in-app and nowhere else. Bus-only; the resolver keeps it one pass.
                await self._emit_leave("leave.approved", request, [], resolver=schedule_on)
                placed_by_year[day.year] = placed_by_year.get(day.year, 0) + 1
                created += 1
        return created

    # --- entitlements ------------------------------------------------------------ #
    async def _recompute_generated_entitlements(self, user_id: uuid.UUID) -> None:
        """Re-derive this user's **generated** entitlements after a contract create/correct/
        terminate (#105, #264).

        The pre-#264 version only *filled missing* years, so a year already seeded from an
        earlier contract stayed frozen: terminating an open-ended contract mid-year left the
        full-year pot in place, and a raise via "terminate old + add new" was ignored for the
        rest of the year. This wipes the auto rows for every affected year and recomputes them
        from the current contracts — so a shorter period prorates down, and a period that no
        longer covers a year loses its pot entirely.

        Which years: the current org-local year, any **future** year the org has already
        generated for other staff (the #105 December-hire policy), **and** every current/future
        year this user already holds a generated row for — that last set is what lets a
        termination *remove* the future-year pot the ended contract used to justify. Past years
        are frozen (§14): a closed year is never re-priced by a later correction.

        ``manual`` rows (``upsert_entitlement``) are never deleted or recreated — they survive
        both the wipe and ``seed_entitlements``'s own "skip what already exists" guard. A year the
        contracts no longer cover is wiped but **not** reseeded: ``seed_entitlements`` would
        otherwise hand a departed employee who still holds ``time.entry.write`` a full
        contract-less-fallback pot. Runs in the contract write's own transaction and, like the
        old seeder, is deliberately not gated on ``leave.entitlement.write`` — the entitlements
        are the consequence of a write the caller was already allowed to make.
        """
        current = (await self._org_today()).year
        contracts = await self._user_contracts(user_id)
        org_future_years = set(
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
        user_generated_years = set(
            (
                await self.ctx.session.execute(
                    select(LeaveEntitlement.year)
                    .where(
                        LeaveEntitlement.org_id == self.ctx.org.id,
                        LeaveEntitlement.user_id == user_id,
                        LeaveEntitlement.year >= current,
                        LeaveEntitlement.source == "generated",
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        for year in sorted({current} | org_future_years | user_generated_years):
            if year < current:
                continue
            stale = (
                (
                    await self.ctx.session.execute(
                        self.entitlements.scoped_select().where(
                            LeaveEntitlement.user_id == user_id,
                            LeaveEntitlement.year == year,
                            LeaveEntitlement.source == "generated",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in stale:
                await self.entitlements.delete(row)
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
        # Either branch claims the row as a deliberate override (#264): a contract-change
        # recompute recreates only ``generated`` rows, so an admin's grant survives it. The update
        # branch matters most — a correction is usually a PUT over an already-generated row.
        if existing is None:
            return await self.entitlements.create(**data.model_dump(), source="manual")
        if Decimal(existing.hours) == data.hours:
            # The member dialog posts every field it rendered, changed or not. A ride-along PUT
            # of the unchanged number is not an override decision, and claiming it as one would
            # permanently opt the row out of the #264 contract recompute.
            if (data.note or None) == (existing.note or None):
                return existing
            return await self.entitlements.update(existing, note=data.note)
        return await self.entitlements.update(
            existing, hours=data.hours, note=data.note, source="manual"
        )

    async def delete_entitlement(
        self, *, user_id: uuid.UUID, leave_type_id: uuid.UUID, year: int
    ) -> None:
        """Drop a pot so generation derives it afresh — the revert the manual badge promises
        ("clear it to let it be derived again"), which until now existed only in the tooltip.

        Deleting alone would leave the pot absent until some later read happened to seed it, so
        the re-derivation runs here, in the same transaction — but only inside the current/next-
        year window every other seeding touch honours: a past year is never backfilled (§14),
        so clearing a historical pot simply removes it. A (user, type, year) the contracts earn
        nothing for stays absent either way.
        """
        self.ctx.require("leave.entitlement.write")
        await self._member_or_404(user_id)
        existing = await self.ctx.session.scalar(
            self.entitlements.scoped_select().where(
                LeaveEntitlement.user_id == user_id,
                LeaveEntitlement.leave_type_id == leave_type_id,
                LeaveEntitlement.year == year,
            )
        )
        if existing is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await self.entitlements.delete(existing)
        current = (await self._org_today()).year
        if current <= year <= current + 1:
            await self.seed_entitlements(year, only_users={user_id})

    async def generate_entitlements(self, year: int) -> int:
        """Create missing entitlements for a year (#65) — the guarded bulk endpoint."""
        self.ctx.require("leave.entitlement.write")
        return await self.seed_entitlements(year)

    async def seed_entitlements(
        self,
        year: int,
        *,
        only_users: set[uuid.UUID] | None = None,
        skip_users: set[uuid.UUID] | None = None,
    ) -> int:
        """The generation core: create **missing** entitlements for a year (#65).

        Two families of balance-tracked type:
          * ``default_weeks`` types (statutory/extra vacation) → ``weeks × contract hours``,
            **prorated** over the days each contract covers of the year;
          * ``accrues_schedule_gap`` types (free time / vrije tijd) → the contract's own
            ``free_time_hours_per_week`` × the weeks it runs in the year, rounded to the nearest
            half day. That figure is ``max(0, norm − contract hours)`` unless the contract records
            one (``_contract_free_time``): the derived rule (#282) is right for a 36-h contract
            worked as a nominal 40-h week and wrong for a 32-h part-timer already working four
            days, so it has to be sayable per contract rather than per org.

        Contract hours are the legal number, not the scheduled week: a 38-hour contract gets
        ``4 × 38`` statutory hours and ``(40 − 38) × weeks`` of free time. An employee who has
        **never** had a contract falls back to the pre-#65 behaviour — a full year on their
        *scheduled* hours — so upgrading moves nobody's balance and needs no contract backfill.

        "Who is staff for year N" is anyone with a contract overlapping the year, unioned with the
        legacy ``time.entry.write`` holders so a contract-less org still generates. But the union
        only makes someone *eligible*: an employee who **has** contracts, none of which cover year
        N (they left before it, or start after), earns nothing for N — only a genuinely
        contract-less employee takes the scheduled-hours fallback (#264).

        A **freelance** period is a contract for that test and earns nothing for either family: a
        ZZP'er invoices their hours and holds no entitlement, and their period is exactly what
        keeps them out of the contract-less fallback they would otherwise fall into on the
        strength of holding ``time.entry.write``. A negotiated paid-days arrangement is still
        expressible — as a ``manual`` entitlement, which recompute never touches.

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
            (
                await self.ctx.session.execute(
                    # Ordered, because the free-time rounding below reads "the last period of the
                    # year" to pick a working day to measure against. An unordered fetch made that
                    # whichever row Postgres returned last.
                    self.contracts.scoped_select().order_by(
                        EmploymentContract.user_id, EmploymentContract.start_date.asc()
                    )
                )
            )
            .scalars()
            .all()
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
        if skip_users:
            # A read-triggered seeding (the roster, #282) leaves anyone who already has rows for
            # the year completely alone — the same "an admin who deliberately zeroed a grant
            # keeps their zero" rule as _ensure_entitlements. The explicit Genereer/cron paths
            # pass nothing here and still fill per-type gaps.
            staff -= skip_users

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
        # Free time is the shortfall from the full-time norm — the org's default week (#282), not
        # the employee's own scheduled week. Fetched once: it is the same figure for everyone.
        full_time_norm = sched.week_hours(default)
        rows = (await self.ctx.session.execute(self.profiles.scoped_select())).scalars().all()
        by_user = {p.user_id: p for p in rows}
        year_days = (date(year, 12, 31) - date(year, 1, 1)).days + 1

        created = 0
        for user_id in staff:
            has_any_contract = bool(contracts_by_user.get(user_id))
            # Only payroll periods earn. A freelance one still counts as *having* a contract,
            # which is what keeps the legacy fallback below out of reach for a freelancer who
            # also holds ``time.entry.write``: the fallback exists for someone who has never had
            # a contract at all, and handing a ZZP'er a year of statutory hours on the strength
            # of a permission would be exactly the pot this change exists to not create.
            contracts_year = self._accruing(
                self._contracts_in_year(contracts_by_user.get(user_id, []), year)
            )
            schedule, scheduled_week, _ = self._effective(by_user.get(user_id), default)
            # Half-day rounding needs *a* working day to measure against. The week now lives on
            # the contract, so prefer the one in force at the end of this year's coverage — the
            # arrangement the free days will actually be taken under. One choice has to be made
            # (a year can hold several contracts); this is the one that matches the days being
            # placed, and it falls back to the profile week for a contract carrying none.
            if contracts_year:
                last_week = sched.parse(contracts_year[-1].schedule)
                if last_week is not None:
                    schedule = last_week
            avg_day = sched.average_day_hours(schedule)

            for leave_type in weeks_types:
                if (user_id, leave_type.id) in existing:
                    continue
                weeks = leave_type.default_weeks or Decimal(0)
                if contracts_year:
                    hours = sum(
                        (
                            weeks
                            # Never ``None`` here: only a freelance period may record no agreed
                            # hours, and ``_accruing`` has already dropped those.
                            * (c.contract_hours_per_week or Decimal(0))
                            * Decimal(self._year_overlap_days(c, year))
                            / Decimal(year_days)
                            for c in contracts_year
                        ),
                        Decimal(0),
                    ).quantize(Decimal("0.01"))
                elif has_any_contract:
                    # Staff by the legacy union, but this employee's own contracts don't cover the
                    # year (they left before it, or start later): they are not staff *for this
                    # year*, so no pot. Without this, a terminated employee who still holds
                    # ``time.entry.write`` would be handed a full contract-less pot on the next
                    # balance read — undoing a termination's re-prorate (#264). The fallback below
                    # is only for a user who has never had a contract at all.
                    continue
                else:
                    hours = (weeks * scheduled_week).quantize(Decimal("0.01"))
                await self.entitlements.create(
                    user_id=user_id,
                    leave_type_id=leave_type.id,
                    year=year,
                    hours=hours,
                    source="generated",
                )
                created += 1

            for leave_type in gap_types:
                if (user_id, leave_type.id) in existing:
                    continue
                total = Decimal(0)
                for c in contracts_year:
                    gap = self._contract_free_time(c, full_time_norm)
                    if gap <= 0:
                        continue
                    total += gap * Decimal(self._year_overlap_days(c, year)) / Decimal(7)
                hours = self._round_half_day(total, avg_day) if total > 0 else Decimal("0.00")
                if hours <= 0:
                    # Contract meets the norm → no free time. Don't clutter the balance with a
                    # zero row.
                    continue
                await self.entitlements.create(
                    user_id=user_id,
                    leave_type_id=leave_type.id,
                    year=year,
                    hours=hours,
                    source="generated",
                )
                created += 1
        return created

    # --- balances / the pot ledger (#265) --------------------------------------------- #
    @staticmethod
    def _pot_expiry(carry_over_months: int | None, accrual_year: int) -> date | None:
        """When a pot accrued in ``accrual_year`` lapses (#265).

        Hours are fully usable through the end of their accrual year, then carry into the next and
        lapse ``carry_over_months`` after it begins: NL statutory (6) → 1 July of the next year,
        extra-statutory (60) → 1 January five years on, free time (0) → 1 January of next year.
        ``None`` — the column's "never" — means the pot never expires. Config, not law: the months
        come from ``leave_types``, so another jurisdiction is a data change, not a code one (§14).
        """
        if carry_over_months is None:
            return None
        return LeaveService._add_months(date(accrual_year + 1, 1, 1), carry_over_months)

    async def _entitlement_rows_upto(
        self, user_id: uuid.UUID, upto_year: int
    ) -> Sequence[LeaveEntitlement]:
        """Every entitlement pot for this user up to and including ``upto_year`` (#265).

        Carry-over means a year's balance depends on prior years' unused pots, so this spans years
        rather than one. Org- and user-scoped; the caller is already access-checked (``balances``
        gates on ``leave.request.read`` and resolves the effective user first).
        """
        stmt = self.entitlements.scoped_select().where(
            LeaveEntitlement.user_id == user_id, LeaveEntitlement.year <= upto_year
        )
        return (await self.ctx.session.execute(stmt)).scalars().all()

    async def _occupying_requests_upto(
        self, user_id: uuid.UUID, upto_year: int
    ) -> Sequence[LeaveRequest]:
        """This user's occupying (pending/approved) requests that start in ``upto_year`` or before.

        One query, grouped in Python (bounded to one person's leave), so consumption can be
        allocated across pots by year without an aggregate-per-year round trip (PERFORMANCE.md).
        """
        stmt = self.requests.scoped_select().where(
            LeaveRequest.user_id == user_id,
            LeaveRequest.status.in_(_OCCUPYING),
            LeaveRequest.start_date < date(upto_year + 1, 1, 1),
        )
        return (await self.ctx.session.execute(stmt)).scalars().all()

    @staticmethod
    def _allocate_pots(
        pots: list[dict], consumption: list[tuple[date, Decimal]], display_year: int
    ) -> Decimal:
        """FIFO-by-expiry consumption across one group's pots (#265) — "favour the employee" made
        concrete.

        Each consumption item is ``(request start date, hours)``, processed chronologically and
        drawn from the pots still valid **on that date** (accrued by then, not yet expired)
        **soonest-expiry-first**, so short-lived statutory hours are spent before long-lived
        extra ones and nothing lapses that could have been used. Validity is per *date*, not per
        year: a statutory pot that lapses 1 July may fund a June request but never an August one
        — a year-granular pass quietly let post-expiry requests spend hours that had already
        lapsed, over-reporting what remained. Mutates each pot's ``allocated`` and snapshots
        ``allocated_before`` — allocation from requests dated strictly before ``display_year``,
        i.e. the carry-in state at the start of the shown year. Returns the ``display_year``
        over-request shortfall (consumption no pot could cover), which makes that year read
        negative (#109); a shortfall in any other year stays in its own year and never dents a
        later fresh grant.
        """
        display_start = date(display_year, 1, 1)
        snapshotted = False
        shortfall = Decimal(0)
        for start, hours in sorted(consumption, key=lambda item: item[0]):
            if start >= display_start and not snapshotted:
                for pot in pots:
                    pot["allocated_before"] = pot["allocated"]
                snapshotted = True
            need = hours
            if need <= 0:
                continue
            candidates = [
                pot
                for pot in pots
                if pot["accrual_year"] <= start.year
                and (pot["expiry"] is None or pot["expiry"] > start)
            ]
            candidates.sort(key=lambda pot: (pot["expiry"] or date.max, pot["accrual_year"]))
            for pot in candidates:
                available = pot["granted"] - pot["allocated"]
                if available <= 0:
                    continue
                take = min(available, need)
                pot["allocated"] += take
                need -= take
                if need <= 0:
                    break
            if need > 0 and start.year == display_year:
                shortfall += need
        if not snapshotted:
            for pot in pots:
                pot["allocated_before"] = pot["allocated"]
        return shortfall

    async def _ledger(
        self, *, year: int, uid: uuid.UUID, exclude_request_id: uuid.UUID | None = None
    ) -> tuple[list[LeaveBalance], list[LeaveGroupBalance]]:
        """The pot ledger (#265) for one employee: carry-over + expiry over every tracked type,
        yielding both the per-type balances (shape unchanged) and the combined per-group balances.

        First touch of an ungenerated current/next-year pot seeds it (#108); prior years are read
        for carry-over, never seeded (history is not backfilled, §14). The assembly itself is
        :meth:`_assemble_ledger`, shared with the batched roster read (#282).

        ``exclude_request_id`` prices the ledger *as if that request did not exist* — the edit
        preview's give-back (#109): the request's own hours leave consumption before the ledger
        runs, so the credit flows through the same carry-over and expiry rules as everything
        else, which a flat "add its hours back" cannot do across a year boundary.
        """
        await self._ensure_entitlements(uid, year)
        tracked = [t for t in await self.list_types() if t.tracks_balance]
        if not tracked:
            return [], []
        today = await self._org_today()
        occ_reqs = [
            r
            for r in await self._occupying_requests_upto(uid, year)
            if r.id != exclude_request_id
        ]
        return self._assemble_ledger(
            uid=uid,
            year=year,
            tracked=tracked,
            today=today,
            ent_rows=await self._entitlement_rows_upto(uid, year),
            occ_reqs=occ_reqs,
        )

    async def group_balances_all(self, *, year: int) -> list[LeaveGroupBalance]:
        """Every member's combined balances in a handful of bulk queries — the manager team table
        (#282). Each carries its ``user_id`` so the roster keys a figure to a member.

        Seeds an ungenerated current/next-year pot exactly like the single-user ledger does
        (#108): §14 promises the manager's table and the employee's own page can never show a
        different Vakantieverlof number, and a roster that skips the seeding shows "0 / 0" for
        every member nobody's own read happened to touch yet. Idempotent — only missing rows are
        created — so after the first roster view of a new year it costs reads only. The per-user
        assembly (carry-over, FIFO-by-expiry, lapse) is identical — only the fetch is batched,
        two org-wide selects instead of two per member (docs/PERFORMANCE.md).

        Whose rows: the union of members with an entitlement pot up to the year and members with an
        occupying request in it. A member with neither has an all-zero balance the caller renders
        straight from the type config, so there is nothing to compute for them.
        """
        tracked = [t for t in await self.list_types() if t.tracks_balance]
        if not tracked:
            return []
        tracked_ids = {t.id for t in tracked}
        today = await self._org_today()
        # The same guards as _ensure_entitlements, batched: current or next org year only (never
        # a backfill of history), never a write for a caller who cannot hold leave, and a member
        # who already has rows for the year — even a deliberate zero — is left alone.
        if self.ctx.can("leave.request.write") and today.year <= year <= today.year + 1:
            have_rows = set(
                (
                    await self.ctx.session.execute(
                        select(LeaveEntitlement.user_id)
                        .where(
                            LeaveEntitlement.org_id == self.ctx.org.id,
                            LeaveEntitlement.year == year,
                        )
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            await self.seed_entitlements(year, skip_users=have_rows)

        ent_by_user: dict[uuid.UUID, list[LeaveEntitlement]] = {}
        ent_rows = (
            await self.ctx.session.execute(
                self.entitlements.scoped_select().where(LeaveEntitlement.year <= year)
            )
        ).scalars().all()
        for ent in ent_rows:
            if ent.leave_type_id in tracked_ids:
                ent_by_user.setdefault(ent.user_id, []).append(ent)

        occ_by_user: dict[uuid.UUID, list[LeaveRequest]] = {}
        occ_rows = (
            await self.ctx.session.execute(
                self.requests.scoped_select().where(
                    LeaveRequest.status.in_(_OCCUPYING),
                    LeaveRequest.start_date < date(year + 1, 1, 1),
                )
            )
        ).scalars().all()
        for req in occ_rows:
            if req.leave_type_id in tracked_ids:
                occ_by_user.setdefault(req.user_id, []).append(req)

        result: list[LeaveGroupBalance] = []
        for uid in ent_by_user.keys() | occ_by_user.keys():
            _, grouped = self._assemble_ledger(
                uid=uid,
                year=year,
                tracked=tracked,
                today=today,
                ent_rows=ent_by_user.get(uid, []),
                occ_reqs=occ_by_user.get(uid, []),
            )
            result.extend(grouped)
        return result

    def _assemble_ledger(
        self,
        *,
        uid: uuid.UUID,
        year: int,
        tracked: list[LeaveType],
        today: date,
        ent_rows: Sequence[LeaveEntitlement],
        occ_reqs: Sequence[LeaveRequest],
    ) -> tuple[list[LeaveBalance], list[LeaveGroupBalance]]:
        """Pure (no I/O) assembly of one employee's pot ledger from already-fetched rows.

        Grouping is by ``balance_group``; a type without one is its own singleton group, so free
        time gains real expiry too (carry 0 → unused hours lapse at year-end). A pot's leftover
        belongs to exactly one type, so **group remaining is the sum of its types' remaining** —
        the two views cannot disagree. Shared by the single-user ledger and the batched roster read
        (#282) so both compute balances the identical way.
        """
        tracked_ids = {t.id for t in tracked}
        carry_by_type = {t.id: t.carry_over_months for t in tracked}
        year_start = date(year, 1, 1)
        soon_cutoff = self._add_months(today, _EXPIRING_SOON_MONTHS)

        pots_by_type: dict[uuid.UUID, list[dict]] = {tid: [] for tid in tracked_ids}
        for ent in ent_rows:
            if ent.leave_type_id not in tracked_ids:
                continue
            pots_by_type[ent.leave_type_id].append(
                {
                    "type_id": ent.leave_type_id,
                    "accrual_year": ent.year,
                    "granted": Decimal(ent.hours),
                    "expiry": self._pot_expiry(carry_by_type[ent.leave_type_id], ent.year),
                    "allocated": Decimal(0),
                    "allocated_before": Decimal(0),
                }
            )

        # Occupying hours per (type, start-year, status): per-type approved/pending for the display
        # year, and — summed over a group — that group's consumption per year for the FIFO pass.
        occ: dict[tuple[uuid.UUID, int, str], Decimal] = {}
        for req in occ_reqs:
            if req.leave_type_id not in tracked_ids:
                continue
            occ_key = (req.leave_type_id, req.start_date.year, req.status)
            occ[occ_key] = occ.get(occ_key, Decimal(0)) + Decimal(req.hours)

        groups: dict[str, list[LeaveType]] = {}
        for t in tracked:
            groups.setdefault(t.balance_group or f"\x00type:{t.id}", []).append(t)

        per_type: dict[uuid.UUID, LeaveBalance] = {}
        grouped: list[tuple[int, str, LeaveGroupBalance]] = []
        for gtypes in groups.values():
            group_value = gtypes[0].balance_group  # None for a synthetic singleton group
            gtype_ids = [t.id for t in gtypes]
            gtype_id_set = set(gtype_ids)
            representative = min(
                gtypes,
                key=lambda t: (
                    t.carry_over_months if t.carry_over_months is not None else 10**9,
                    t.position,
                    t.key,
                ),
            )
            pots = [pot for tid in gtype_ids for pot in pots_by_type[tid]]

            consumption = [
                (req.start_date, Decimal(req.hours))
                for req in occ_reqs
                if req.leave_type_id in gtype_id_set
            ]

            shortfall = self._allocate_pots(pots, consumption, year)

            entitled_by_type = {tid: Decimal(0) for tid in gtype_ids}
            remaining_by_type = {tid: Decimal(0) for tid in gtype_ids}
            group_lapsed = Decimal(0)
            group_expiring = Decimal(0)
            breakdown: list[LeavePotBreakdown] = []
            for pot in sorted(
                pots, key=lambda pot: (pot["expiry"] or date.max, pot["accrual_year"])
            ):
                granted = pot["granted"]
                leftover = max(Decimal(0), granted - pot["allocated"])
                expiry = pot["expiry"]
                # Dead by *today*, or dead by the start of the year being viewed — a next-year
                # read taken in December must not count a pot the FIFO pass above will never let
                # that year spend (free time carries 0: its pot dies the exact moment the viewed
                # year begins, and "64 u over van 52 u" is an impossible balance).
                expired = expiry is not None and expiry <= max(today, year_start)

                if pot["accrual_year"] == year:
                    entitled_contrib = granted
                elif expiry is None or expiry > year_start:
                    # A prior-year pot still alive at the start of the shown year: its carry-in is
                    # whatever remained after earlier years drew on it.
                    entitled_contrib = max(Decimal(0), granted - pot["allocated_before"])
                else:
                    entitled_contrib = Decimal(0)
                entitled_by_type[pot["type_id"]] += entitled_contrib

                remaining_contrib = Decimal(0) if expired else leftover
                remaining_by_type[pot["type_id"]] += remaining_contrib

                if expired and expiry is not None and expiry > year_start:
                    group_lapsed += leftover
                elif not expired and expiry is not None and expiry <= soon_cutoff:
                    group_expiring += leftover

                breakdown.append(
                    LeavePotBreakdown(
                        leave_type_id=pot["type_id"],
                        accrual_year=pot["accrual_year"],
                        entitled_hours=granted,
                        remaining_hours=remaining_contrib,
                        expires_on=expiry,
                        expired=expired,
                    )
                )

            # An over-request no pot could cover reads as negative remaining, carried on the group's
            # representative (soonest-expiring) type so Σ per-type remaining == group remaining.
            remaining_by_type[representative.id] -= shortfall

            for t in gtypes:
                per_type[t.id] = LeaveBalance(
                    leave_type_id=t.id,
                    year=year,
                    entitled_hours=entitled_by_type[t.id],
                    approved_hours=occ.get(
                        (t.id, year, LeaveRequestStatus.APPROVED.value), Decimal(0)
                    ),
                    pending_hours=occ.get(
                        (t.id, year, LeaveRequestStatus.PENDING.value), Decimal(0)
                    ),
                    remaining_hours=remaining_by_type[t.id],
                    balance_group=t.balance_group,
                )

            label = _GROUP_LABELS.get(group_value) if group_value else None
            if label is None:
                label = dict(representative.label_i18n or {})
            grouped.append(
                (
                    representative.position,
                    representative.key,
                    LeaveGroupBalance(
                        user_id=uid,
                        group=group_value,
                        leave_type_ids=gtype_ids,
                        label_i18n=label,
                        year=year,
                        entitled_hours=sum(entitled_by_type.values(), Decimal(0)),
                        approved_hours=sum(
                            (per_type[t.id].approved_hours for t in gtypes), Decimal(0)
                        ),
                        pending_hours=sum(
                            (per_type[t.id].pending_hours for t in gtypes), Decimal(0)
                        ),
                        remaining_hours=sum(remaining_by_type.values(), Decimal(0)),
                        lapsed_hours=group_lapsed,
                        expiring_soon_hours=group_expiring,
                        pots=breakdown,
                    ),
                )
            )

        ordered_per_type = [per_type[t.id] for t in tracked]
        grouped.sort(key=lambda item: (item[0], item[1]))
        return ordered_per_type, [gb for _, _, gb in grouped]

    async def balances(
        self,
        *,
        year: int,
        user_id: uuid.UUID | None = None,
        exclude_request_id: uuid.UUID | None = None,
    ) -> list[LeaveBalance]:
        """Per-type balances (#265): entitled + carried − approved − pending, expiry-aware.

        Shape unchanged so ``preview``, ``summary``, the recurring generator and existing clients
        keep working; ``remaining_hours`` now reflects the FIFO-by-expiry pot ledger.
        """
        uid = self._effective_user_id(user_id)
        per_type, _ = await self._ledger(
            year=year, uid=uid, exclude_request_id=exclude_request_id
        )
        return per_type

    # --- free time overview (#65's card, and the wizard's reconciliation) ---------------- #
    async def free_time_overview(
        self, *, year: int, user_id: uuid.UUID | None = None
    ) -> FreeTimeOverview:
        """Pot, placed days and overhang for one employee's free time, in one read.

        The per-type balance cannot answer the questions people actually ask of free time. Once
        the generator has laid every day down, entitled and approved are equal and it reports
        "0 h over" — true, and no use whatsoever for "when is my next day off" or "does the pot
        still cover what is on my calendar". Both of those live here.

        Five queries at most regardless of how many days are placed (docs/PERFORMANCE.md): the
        types, the entitlement ledger, the requests, the contracts and the profile behind the
        schedule. ``leave.request.read`` scoping comes from ``_effective_user_id``, so a member
        reads their own and a manager anyone's — the same rule as every other leave read.
        """
        uid = self._effective_user_id(user_id)
        types = [t for t in await self.list_types() if t.accrues_schedule_gap]
        schedule = await self.effective_schedule(uid)
        blank = FreeTimeOverview(
            user_id=uid,
            year=year,
            leave_type_ids=[],
            entitled_hours=Decimal("0.00"),
            placed_hours=Decimal("0.00"),
            taken_hours=Decimal("0.00"),
            upcoming_hours=Decimal("0.00"),
            unplaced_hours=Decimal("0.00"),
            overhang_hours=Decimal("0.00"),
            hours_per_day=sched.average_day_hours(schedule),
            next_date=None,
            days=[],
            overhang=[],
        )
        if not types:
            # The tenant deactivated free time. Zeroes, not an error: the card simply renders
            # nothing, and the wizard's third step has nothing to offer.
            return blank

        type_ids = [t.id for t in types]
        await self._ensure_entitlements(uid, year)
        balances = {b.leave_type_id: b for b in await self.balances(year=year, user_id=uid)}
        entitled = sum(
            (balances[tid].entitled_hours for tid in type_ids if tid in balances), Decimal(0)
        )

        rows = (
            (
                await self.ctx.session.execute(
                    self.requests.scoped_select()
                    .where(
                        LeaveRequest.user_id == uid,
                        LeaveRequest.leave_type_id.in_(type_ids),
                        LeaveRequest.status.in_(_OCCUPYING),
                        LeaveRequest.start_date >= date(year, 1, 1),
                        LeaveRequest.start_date <= date(year, 12, 31),
                    )
                    .order_by(LeaveRequest.start_date.asc())
                )
            )
            .scalars()
            .all()
        )

        today = await self._org_today()

        def as_day(request: LeaveRequest) -> FreeTimeDay:
            start, end = self._resolved_window(request, schedule)
            return FreeTimeDay(
                request_id=request.id,
                date=request.start_date,
                hours=request.hours,
                start_time=start,
                end_time=end,
                from_pattern=request.recurring_day_id is not None,
            )

        placed = sum((Decimal(r.hours) for r in rows), Decimal(0))
        taken = sum((Decimal(r.hours) for r in rows if r.end_date < today), Decimal(0))
        upcoming_rows = [r for r in rows if r.end_date >= today]

        overhang_hours = max(Decimal(0), placed - entitled)
        # Peel the **latest** future pattern-generated days until the excess is covered: the most
        # recently planned days are the least disruptive to withdraw, and a day the employee
        # booked by hand is theirs — a contract correction must not silently eat it.
        overhang: list[FreeTimeDay] = []
        excess = overhang_hours
        for request in sorted(
            (r for r in upcoming_rows if r.recurring_day_id is not None),
            key=lambda r: r.start_date,
            reverse=True,
        ):
            if excess <= 0:
                break
            overhang.append(as_day(request))
            excess -= Decimal(request.hours)

        return blank.model_copy(
            update={
                "leave_type_ids": type_ids,
                "entitled_hours": entitled.quantize(Decimal("0.01")),
                "placed_hours": placed.quantize(Decimal("0.01")),
                "taken_hours": taken.quantize(Decimal("0.01")),
                "upcoming_hours": (placed - taken).quantize(Decimal("0.01")),
                "unplaced_hours": max(Decimal(0), entitled - placed).quantize(Decimal("0.01")),
                "overhang_hours": overhang_hours.quantize(Decimal("0.01")),
                "next_date": upcoming_rows[0].start_date if upcoming_rows else None,
                "days": [as_day(r) for r in upcoming_rows],
                "overhang": overhang,
            }
        )

    async def withdraw_free_time(
        self, request_ids: Sequence[uuid.UUID]
    ) -> tuple[int, list[uuid.UUID]]:
        """Cancel free days the pot no longer covers, one by one through :meth:`cancel`.

        Through ``cancel`` and not a bulk UPDATE, so every rule still holds: the past stays
        locked, self-approval policy applies, and a cancelled day emits ``leave.cancelled`` so
        the Google mirror removes its event. An id that cannot be cancelled is **skipped, not
        raised** — the caller is confirming a list it was shown, and one stale row should not
        abandon the rest of a withdrawal.
        """
        cancelled = 0
        skipped: list[uuid.UUID] = []
        for request_id in request_ids:
            try:
                await self.cancel(request_id)
            except AppError:
                skipped.append(request_id)
            else:
                cancelled += 1
        return cancelled, skipped

    async def group_balances(
        self,
        *,
        year: int,
        user_id: uuid.UUID | None = None,
        all_users: bool = False,
        exclude_request_id: uuid.UUID | None = None,
    ) -> list[LeaveGroupBalance]:
        """The employee-facing combined balances (#265): one figure per group, per-pot breakdown
        alongside. ``vacation_statutory`` + ``vacation_extra`` roll up into one "Vakantieverlof".

        ``all_users`` returns every member's groups for the manager team table (#282), each tagged
        with its ``user_id`` — but only for a caller who may read anyone's leave
        (``leave.request.read:any``, like ``list_entitlements``). Without that scope it degrades to
        the caller's own, never another's, exactly as a bare read would.
        """
        if all_users and self.ctx.can("leave.request.read", scope="any"):
            return await self.group_balances_all(year=year)
        uid = self._effective_user_id(user_id)
        _, grouped = await self._ledger(
            year=year, uid=uid, exclude_request_id=exclude_request_id
        )
        return grouped

    # --- the hour calculation (#48) --------------------------------------------------- #
    def _breakdown(
        self,
        *,
        schedule_on: Callable[[date], sched.WorkSchedule],
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

        The week is resolved **per day**, not once for the span: the working week lives on the
        employment contract, and a span can cross a contract boundary (someone who went from four
        days to five on 1 March). ``schedule_on`` answers from memory, so this stays one pass.
        """
        rows: list[tuple[date, int, str | None]] = []
        day = start_date
        while day <= end_date:
            work_day = schedule_on(day).day(day.weekday())
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
        *,
        resolver: Callable[[date], sched.WorkSchedule] | None = None,
    ) -> tuple[Decimal, list[LeaveDayHours]]:
        """``(total hours, per-day breakdown)`` — the one place a leave hour is decided.

        Rounds to two decimals **once**, on the summed minutes: rounding each day and adding the
        results is how a 40-hour week becomes 39,99.

        ``resolver`` lets a caller that prices many spans for the **same** employee build the
        ``date → week`` lookup once and hand it in — the recurring generator walks a year of
        occurrences, and rebuilding it per day would be two queries a day (docs/PERFORMANCE.md).
        Omit it and one is built here.
        """
        schedule_on = resolver or await self.schedule_resolver(user_id)
        holidays_off = await self.active_holidays_between(start_date, end_date)
        rows = self._breakdown(
            schedule_on=schedule_on,
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
        per_day = sched.average_day_hours(await self.effective_schedule(uid, data.start_date))
        days = (hours / per_day).quantize(Decimal("0.01")) if per_day else Decimal("0")
        # Tell the form whether saving would need (re-)approval (#72): a past span always does; a
        # future one only if the chosen type requires approval. The form knows the request's
        # current status, so it decides whether to warn "this moves it back to pending".
        # On an edit the rule mirrors update(): the span touches the past when the *new* span
        # reaches before today or the *original* already did — pulling leave out of history is
        # as guarded as writing into it, and the form must warn before submit, not 403 after
        # (#114).
        today = await self._org_today()
        touches_past = data.start_date < today
        current: LeaveRequest | None = None
        if data.request_id is not None:
            current = await self.requests.get_or_404(data.request_id)
            if current.user_id == uid and current.start_date < today:
                touches_past = True
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
                # Editing: the balance is priced *as if the edited request did not exist*, so its
                # current hours flow back through the ledger's own carry-over and expiry rules.
                # A flat "add its hours back" broke twice: it skipped the give-back entirely when
                # the edit crossed a year boundary (the freed December hours do reach January via
                # carry-over), and it over-credited when the freed hours sat in a pot that lapses
                # before the new span's year. Exclusion is also pool-agnostic for free — removing
                # the request from consumption cannot inflate a pool it never drew from.
                exclude = (
                    current.id
                    if current is not None
                    and current.user_id == uid
                    and current.status in _OCCUPYING
                    else None
                )
                if leave_type.balance_group:
                    # A grouped type spends one combined "Vakantieverlof" pool (#265) — the warning
                    # is the group remaining, not the single stored type's.
                    group = next(
                        (
                            g
                            for g in await self.group_balances(
                                year=year, user_id=uid, exclude_request_id=exclude
                            )
                            if leave_type.id in g.leave_type_ids
                        ),
                        None,
                    )
                    remaining = group.remaining_hours if group else Decimal(0)
                else:
                    balances = {
                        b.leave_type_id: b
                        for b in await self.balances(
                            year=year, user_id=uid, exclude_request_id=exclude
                        )
                    }
                    balance = balances.get(leave_type.id)
                    remaining = balance.remaining_hours if balance else Decimal(0)
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
        # Setting `hours` by hand is an approver's act — and while self-approval is off it is an
        # approver act on your OWN request, which needs a second approver just like deciding does
        # (audit F20).
        if data.hours_override is not None:
            self.ctx.require("leave.request.approve")
            if not await self._acts_as_approver(uid):
                raise AppError("self_approval", "errors.leave_self_approval", status_code=403)
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
        # Snapshotted alongside the priced hours, so the displayed window is forever the one
        # this schedule gave — a later schedule change must not rewrite past leave (#64).
        resolved_start, resolved_end = self._resolve_bounds(
            await self.effective_schedule(uid, data.start_date),
            data.start_date,
            data.start_time,
            data.end_date,
            data.end_time,
        )
        request = await self.requests.create(
            user_id=uid,
            leave_type_id=data.leave_type_id,
            start_date=data.start_date,
            start_time=data.start_time,
            end_date=data.end_date,
            end_time=data.end_time,
            resolved_start_time=resolved_start,
            resolved_end_time=resolved_end,
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
        else:
            # Born approved, so the approval subscribers hear about it now — nobody will ever
            # call decide() on it. Without this the Google mirror only saw an auto-approved
            # absence if someone happened to *move* it later (leave.updated), which pushed the
            # moved sick day and skipped every untouched one. Bus-only: no recipients — a
            # registration notifies nobody, exactly as above.
            await self._emit_leave("leave.approved", request, [])
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
        self,
        event: str,
        request: LeaveRequest,
        recipients: Sequence[uuid.UUID],
        *,
        resolver: Callable[[date], sched.WorkSchedule] | None = None,
    ) -> None:
        """Announce a leave decision on the bus (CLAUDE.md §6 — no cross-module imports).

        ``user_id`` and the resolved window ride along so a subscriber that mirrors leave
        elsewhere (the Google Calendar push, issue #22) never reads this module's internals.
        ``resolver`` lets a bulk caller (the recurring generator, which emits once per placed
        day) reuse its already-built ``date → week`` lookup instead of two queries per emit.
        """
        payload: dict[str, Any] = {
            "leave_request_id": request.id,
            "user_id": request.user_id,
            "leave_type_id": request.leave_type_id,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "start_time": request.resolved_start_time or request.start_time,
            "end_time": request.resolved_end_time or request.end_time,
            "hours": request.hours,
            "_recipients": list(recipients),
        }
        if event in ("leave.approved", "leave.updated"):
            # The Google Calendar push (#148) describes the span day by day — 2 u on
            # Thursday, 5 u on Friday — which only this module can compute. Decide-time
            # work, never a list render; zero-hour days say nothing worth syncing.
            _, breakdown = await self.compute_hours(
                request.user_id,
                request.start_date,
                request.resolved_start_time or request.start_time,
                request.end_date,
                request.resolved_end_time or request.end_time,
                resolver=resolver,
            )
            payload["breakdown"] = [
                {"date": row.date.isoformat(), "hours": float(row.hours)}
                for row in breakdown
                if row.hours
            ]
            # #270 is a *type-level* choice, and the mirror follows it in both directions. A
            # "timed" type is drawn as an hour block on every calendar, so the Google mirror
            # must push a timed event — not an all-day banner — even for a whole-day request
            # that carries no times of its own (free time / vrije tijd): its scheduled window
            # resolves here, where the schedule lives, so the mirror never reads leave
            # internals (§6). Single-day only; a multi-day span stays all-day everywhere.
            display = await self._type_calendar_display(request.leave_type_id)
            if payload["start_time"] is None or payload["end_time"] is None:
                if (
                    request.start_date == request.end_date
                    and display == LeaveCalendarDisplay.TIMED.value
                ):
                    schedule = (
                        resolver(request.start_date)
                        if resolver is not None
                        else await self.effective_schedule(
                            request.user_id, request.start_date
                        )
                    )
                    start, end = self._single_day_window(
                        request, schedule, (payload["start_time"], payload["end_time"])
                    )
                    payload["start_time"] = start
                    payload["end_time"] = end
            elif display == LeaveCalendarDisplay.ALL_DAY.value:
                # And the inverse: the in-app agenda draws an `all_day` type's part-day absence
                # as a full-width chip, so the mirror pushes an all-day event too — the hours
                # stay in the description's per-day breakdown. Without this, one absence read
                # as a chip in-app and an hour block in Google.
                payload["start_time"] = None
                payload["end_time"] = None
        await emit(event, self.ctx, payload)

    async def _type_calendar_display(self, type_id: uuid.UUID) -> str:
        """This type's agenda-rendering choice (#270), read for the leave→Google mirror without
        loading the whole row. Tenant-scoped by the session's RLS GUC like every other read."""
        display = await self.ctx.session.scalar(
            select(LeaveType.calendar_display).where(LeaveType.id == type_id)
        )
        return display or LeaveCalendarDisplay.ALL_DAY.value

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
        # ``Clock``'s serializer stringifies in model_dump() too; both the hour computation and
        # the TIME columns need the actual time objects, not "15:00".
        if "start_time" in values:
            values["start_time"] = data.start_time
        if "end_time" in values:
            values["end_time"] = data.end_time
        leave_type = await self.types.get_or_404(
            values.get("leave_type_id", request.leave_type_id)
        )
        start_date = values.get("start_date", request.start_date)
        start_time = values.get("start_time", request.start_time)
        end_date = values.get("end_date", request.end_date)
        end_time = values.get("end_time", request.end_time)
        span_changed = (
            start_date != request.start_date
            or start_time != request.start_time
            or end_date != request.end_date
            or end_time != request.end_time
        )
        override = values.get("hours_override", request.hours_override)
        # Setting or clearing the override is an approver's act; leaving a stored one alone isn't.
        # On your own request, while self-approval is off, it needs another approver (audit F20).
        if "hours_override" in data.model_fields_set:
            self.ctx.require("leave.request.approve")
            if not await self._acts_as_approver(request.user_id):
                raise AppError("self_approval", "errors.leave_self_approval", status_code=403)
        elif span_changed and request.hours_override is not None:
            # The override priced one specific span ("four hours agreed on a day they were not
            # scheduled"). A PATCH that moves the span without restating it would keep charging
            # that number for a different absence — with the original approver still attributed —
            # so the stale override is dropped and the new span is priced from the schedule like
            # any other edit (§14: recomputed on every edit). An approver who means to keep it
            # simply restates it in the same PATCH, which the form already does.
            override = None
            values["hours_override"] = None
            values["hours_override_by_user_id"] = None

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
        # changing (computed above, where the override rule needs it too), not merely off the
        # request already touching the past.
        if span_changed:
            await self._ensure_may_backdate(
                touches_past, own=request.user_id == self.ctx.user.id
            )
            # A moved span is repriced, so its window snapshot refreshes with it; an untouched
            # span keeps the window it was written with — a note edit (or a schedule change in
            # between) must never rewrite how existing leave displays (#64).
            (
                values["resolved_start_time"],
                values["resolved_end_time"],
            ) = self._resolve_bounds(
                await self.effective_schedule(request.user_id, start_date),
                start_date,
                start_time,
                end_date,
                end_time,
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
            # still occupies the balance, so no balance regression. The bounce clears decided_*,
            # so the resubmission stamp is what keeps this distinguishable from new leave (#120).
            values["status"] = LeaveRequestStatus.PENDING.value
            values["decided_by_user_id"] = None
            values["decided_at"] = None
            values["decision_note"] = None
            values["resubmitted_at"] = _now()

        was_approved = request.status == LeaveRequestStatus.APPROVED.value
        window_before = (
            request.start_date,
            request.start_time,
            request.end_date,
            request.end_time,
        )
        updated = await self.requests.update(request, **values)
        if bounce:
            await self._emit_leave("leave.requested", updated, await self._approvers_for(updated))
        elif was_approved and updated.status == LeaveRequestStatus.APPROVED.value:
            window_after = (
                updated.start_date,
                updated.start_time,
                updated.end_date,
                updated.end_time,
            )
            if window_after != window_before:
                # An approver's in-place edit of approved leave used to leave the pushed
                # Google event on the old dates (#148). Bus-only event, like leave.cancelled:
                # the calendar mirror refreshes, nobody's inbox is involved.
                await self._emit_leave("leave.updated", updated, [])
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
            # The re-submission marker only describes an *undecided* bounce (#120).
            resubmitted_at=None,
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
        that does not touch the past. "Moving" a free-time day is exactly this: cancel one, create
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
        was_approved = request.status == LeaveRequestStatus.APPROVED.value
        request = await self.requests.update(
            request, status=LeaveRequestStatus.CANCELLED.value
        )
        # Approved leave may have side effects elsewhere (a pushed Google Calendar event,
        # #22) — announce the cancellation so subscribers undo theirs. Bus-only event: it is
        # not in the notifications vocabulary, so nobody's inbox is involved.
        if was_approved:
            await self._emit_leave("leave.cancelled", request, [])
        return request

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
        schedule_on: Callable[[date], sched.WorkSchedule],
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
            schedule_on=schedule_on,
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

        Four extra reads, not four per row: every profile, every contract, the org default and the
        holidays in range are fetched once and the per-day shapes computed in Python
        (docs/PERFORMANCE.md).
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
        # The working week lives on the contract, so the feed needs every contract too — one
        # query for the whole org, resolved in Python per row, never one lookup per request.
        periods_by_user: dict[uuid.UUID, list[tuple[date, date | None, sched.WorkSchedule | None]]]
        periods_by_user = {}
        for contract in (
            (await self.ctx.session.execute(self.contracts.scoped_select())).scalars().all()
        ):
            periods_by_user.setdefault(contract.user_id, []).append(
                (contract.start_date, contract.end_date, sched.parse(contract.schedule))
            )
        # A request may start before `date_from` and end after `date_to`; shape the whole of it.
        span_from = min(req.start_date for req, _ in rows)
        span_to = max(req.end_date for req, _ in rows)
        holidays_off = await self.active_holidays_between(span_from, span_to)
        # One lookup for the whole feed, not one per row: the zone every wall clock below is
        # anchored in (#270). Same shape as the three reads above (docs/PERFORMANCE.md).
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)

        items: list[TeamLeaveItem] = []
        for req, user in rows:
            schedule_on = self._resolver_from(
                periods_by_user.get(req.user_id, ()),
                self._effective(by_user.get(req.user_id), default)[0],
            )
            # The window and the instants describe the request as a whole, so they read the week
            # its **start date** fell under; only the per-day shape below resolves day by day.
            schedule = schedule_on(req.start_date)
            resolved_start, resolved_end = self._resolved_window(req, schedule)
            starts_at, ends_at = self._occupied_instants(
                req, schedule, (resolved_start, resolved_end), zone
            )
            items.append(
                TeamLeaveItem(
                    id=req.id,
                    user_id=req.user_id,
                    user_name=user.full_name or user.email,
                    leave_type_id=req.leave_type_id,
                    start_date=req.start_date,
                    start_time=req.start_time,
                    end_date=req.end_date,
                    end_time=req.end_time,
                    resolved_start_time=resolved_start,
                    resolved_end_time=resolved_end,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    hours=req.hours,
                    status=LeaveRequestStatus(req.status),
                    days=self._shaped_days(req, schedule_on, holidays_off),
                )
            )
        return items

    @staticmethod
    def _single_day_window(
        request: LeaveRequest,
        schedule: sched.WorkSchedule,
        resolved: tuple[time | None, time | None],
    ) -> tuple[time | None, time | None]:
        """The clock window a **single-day** absence covers, for a calendar that draws it by the
        hour (#270). ``(None, None)`` whenever one honestly cannot be drawn.

        Three refusals, each deliberate:

        * **Multi-day spans.** A single window from Monday morning to Friday evening also claims
          Monday night and Wednesday 03:00. ``days`` already breaks those down per day.
        * **A day nobody works.** No scheduled window means no hours to draw. (A whole-day
          request on such a day prices at zero and is refused at write time; a manager's
          ``hours_override`` can still produce one, and it stays a full-day chip.)
        * **An empty window.** Defensive: a start at or after the end would render as a
          zero-height block, which is worse than the all-day form it fell back from.

        The window is the request's own resolved one where it has times, and otherwise the
        scheduled day itself — "whole scheduled day" is exactly what a free-time day is, and
        the only window it could ever be drawn at (#107). Shared by the in-app agenda
        (``_occupied_instants``) and the Google mirror (``_emit_leave``), so both agree.
        """
        if request.start_date != request.end_date:
            return None, None
        start, end = resolved
        if start is None or end is None:
            work_day = schedule.day(request.start_date.weekday())
            if work_day is None:
                return None, None
            # `is None`, not `or`: midnight is a legitimate bound and only happens to be truthy.
            if start is None:
                start = work_day.start
            if end is None:
                end = work_day.end
        if sched.to_minutes(start) >= sched.to_minutes(end):
            return None, None
        return start, end

    @staticmethod
    def _occupied_instants(
        request: LeaveRequest,
        schedule: sched.WorkSchedule,
        resolved: tuple[time | None, time | None],
        zone: ZoneInfo,
    ) -> tuple[datetime | None, datetime | None]:
        """``_single_day_window`` as a UTC instant pair, the field the time grid positions a
        block by (#270).

        ``.replace(tzinfo=...)`` on a ``ZoneInfo`` — never a fixed offset — is what makes an
        08:30 block still start at 08:30 on the two days a year the clocks move.
        """
        start, end = LeaveService._single_day_window(request, schedule, resolved)
        if start is None or end is None:
            return None, None
        return (
            datetime.combine(request.start_date, start).replace(tzinfo=zone),
            datetime.combine(request.start_date, end).replace(tzinfo=zone),
        )

    @staticmethod
    def _resolve_bounds(
        schedule: sched.WorkSchedule,
        start_date: date,
        start_time: time | None,
        end_date: date,
        end_time: time | None,
    ) -> tuple[time | None, time | None]:
        """The concrete clock window a *timed* span covers (#107).

        A ``NULL`` time means "the scheduled day's own bound" (#48) — right for pricing, but
        a calendar chip cannot print a NULL: "until 14:00" resolves its start from the first
        day's schedule, "from 15:00" its end from the last day's. A span with no times at all
        is a whole-day absence and resolves to nothing — stamping 08:30–17:00 on every
        ordinary vacation chip would be noise, not information. An unscheduled day (a
        manager-override case) yields ``None`` for that bound and the display falls back to
        the open-ended form.

        Called at **write time** to snapshot the window onto the request (create/edit and the
        pattern generator — the same moments ``hours`` is priced), so a later schedule change
        never rewrites how past leave displays; the read path only re-resolves rows that
        predate the snapshot columns.
        """
        if start_time is None and end_time is None:
            return None, None
        start = start_time
        if start is None:
            work_day = schedule.day(start_date.weekday())
            start = work_day.start if work_day else None
        end = end_time
        if end is None:
            work_day = schedule.day(end_date.weekday())
            end = work_day.end if work_day else None
        return start, end

    def _resolved_window(
        self, request: LeaveRequest, schedule: sched.WorkSchedule
    ) -> tuple[time | None, time | None]:
        """The request's display window: the write-time snapshot where one exists, else a
        best-effort resolution against the current schedule for rows that predate it."""
        if request.resolved_start_time is not None or request.resolved_end_time is not None:
            return request.resolved_start_time, request.resolved_end_time
        return self._resolve_bounds(
            schedule,
            request.start_date,
            request.start_time,
            request.end_date,
            request.end_time,
        )

    # --- dashboard widget ---------------------------------------------------------------- #
    async def summary(self) -> LeaveSummary:
        # Org-local, like every other "today" in this module (§8): around midnight the UTC date
        # is still yesterday, and the widget would report last year's balance every 1 January.
        today = await self._org_today()
        year = today.year
        schedule, hours_week, _ = await self.profile_for(self.ctx.user.id)
        # The widget's one figure is *verlof* — free time stays out of it (§14): its pot has its
        # own card, and before the generator places the days it would inflate "verlof over" by a
        # whole year of vrije tijd that is not vacation anyone can request.
        gap_ids = {t.id for t in await self.list_types() if t.accrues_schedule_gap}
        balances = await self.balances(year=year)
        remaining = sum(
            (b.remaining_hours for b in balances if b.leave_type_id not in gap_ids), Decimal(0)
        )
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
            next_leave_id=next_leave.id if next_leave else None,
            next_leave_start=next_leave.start_date if next_leave else None,
            next_leave_end=next_leave.end_date if next_leave else None,
        )
