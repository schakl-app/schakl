"""Per-tenant timezone resolution (CLAUDE.md §8).

Timestamps are stored in UTC (``TIMESTAMPTZ``); *reasoning about a local calendar* — which
Monday a week opens on, which year "next December" tops up — needs the tenant's own zone. Each
org carries one on ``org_settings.timezone``; this module is the single place that reads it and
turns a name into a :class:`~zoneinfo.ZoneInfo`, falling back to the instance default rather
than raising, so a job never dies on a stray value.

There is deliberately **no** per-user override yet: the shipped model is one self-hosted agency
in one zone (CLAUDE.md §5). The resolution seam is here so adding a personal override later is a
change of *inputs*, not of every caller.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

DEFAULT_TIMEZONE = settings.default_timezone


@lru_cache(maxsize=1)
def _known() -> frozenset[str]:
    """The IANA zone names this platform's tzdata knows.

    Cached; the set never changes at runtime.
    """
    return frozenset(available_timezones())


def is_valid_timezone(name: str | None) -> bool:
    """True for an IANA zone name the platform can resolve (``Europe/Amsterdam``, ``UTC``)."""
    return bool(name) and name in _known()


def resolve_zoneinfo(name: str | None) -> ZoneInfo:
    """A :class:`ZoneInfo` for ``name``, or the instance default when it is unset/unknown.

    Never raises: a persisted-but-since-removed zone must not crash a per-org cron sweep.
    """
    return ZoneInfo(name if is_valid_timezone(name) else DEFAULT_TIMEZONE)


async def org_timezone_name(session: AsyncSession, org_id) -> str:
    """The org's configured zone name, or the instance default.

    Reads ``org_settings`` on the caller's (RLS-bound) session — safe from a request or a
    per-org job, since both have the GUC set to this org.
    """
    from app.core.models import OrgSettings

    name = await session.scalar(
        select(OrgSettings.timezone).where(OrgSettings.org_id == org_id)
    )
    return name if is_valid_timezone(name) else DEFAULT_TIMEZONE


async def org_zoneinfo(session: AsyncSession, org_id) -> ZoneInfo:
    """The org's zone as a :class:`ZoneInfo`, for local-calendar math in a background job."""
    return resolve_zoneinfo(await org_timezone_name(session, org_id))


def as_instant(value: datetime, zone: ZoneInfo) -> datetime:
    """A datetime a caller handed us, as the instant it names.

    A **naive** value is a wall clock on the org's own calendar — what a person typed into a
    time field, what a spreadsheet column says, what a registration system that knows no zones
    exported — and is read in ``zone``. An **aware** value already names an instant and is kept
    as it is, whatever offset it carries: ``2026-09-19T12:40:00+02:00`` and
    ``2026-09-19T10:40:00Z`` are the same moment and land in the same row.

    This is the one rule that lets the time module store real instants (``TIMESTAMPTZ``, §8)
    while every form, import and agent keeps sending the clock time a person means.
    """
    return value.replace(tzinfo=zone) if value.tzinfo is None else value


def day_start(day: date, zone: ZoneInfo) -> datetime:
    """Local midnight opening ``day`` in ``zone`` — the instant a calendar day's window begins."""
    return datetime.combine(day, time.min, tzinfo=zone)


def day_window(date_from: date | None, date_to: date | None, zone: ZoneInfo) -> tuple[
    datetime | None, datetime | None
]:
    """The half-open instant window ``[from 00:00, to + 1 day 00:00)`` on the org's calendar.

    Either bound may be absent. Built here rather than at each of the fourteen call sites that
    used to spell it out with ``tzinfo=UTC`` — which put an entry logged at 23:30 on the next
    day's timesheet for every tenant ahead of UTC.
    """
    lo = day_start(date_from, zone) if date_from is not None else None
    hi = day_start(date_to, zone) + timedelta(days=1) if date_to is not None else None
    return lo, hi


async def org_today(session: AsyncSession, org_id) -> date:
    """Today on the org's own calendar — which is the only "today" a tenant ever means.

    "Which year does this number count in", "is this overdue": all local-calendar questions, and
    UTC answers them wrong for several hours a day. (``invoicing``, ``domains`` and ``leave``
    each grew a private copy of this before it lived here; new callers use this one.)
    """
    return datetime.now(await org_zoneinfo(session, org_id)).date()
