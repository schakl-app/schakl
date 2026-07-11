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
