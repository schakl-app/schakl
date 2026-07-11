"""Per-tenant timezone (CLAUDE.md §8): the org setting, its validation, and the per-org
local-calendar reasoning in the cron jobs that used to hardcode ``Europe/Amsterdam``."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.models import OrgSettings
from app.core.timezone import (
    DEFAULT_TIMEZONE,
    is_valid_timezone,
    org_zoneinfo,
    resolve_zoneinfo,
)
from app.db import async_session_maker, set_current_org
from app.modules.time.reminders import _week_bounds, previous_week_start
from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member


def test_is_valid_timezone() -> None:
    assert is_valid_timezone("Europe/Amsterdam")
    assert is_valid_timezone("America/New_York")
    assert is_valid_timezone("UTC")
    assert not is_valid_timezone("Mars/Olympus_Mons")
    assert not is_valid_timezone("")
    assert not is_valid_timezone(None)


def test_resolve_zoneinfo_falls_back_never_raises() -> None:
    assert resolve_zoneinfo("America/New_York") == ZoneInfo("America/New_York")
    # A stray/removed value must not crash a per-org sweep — it resolves to the default.
    assert resolve_zoneinfo("not-a-zone") == ZoneInfo(DEFAULT_TIMEZONE)
    assert resolve_zoneinfo(None) == ZoneInfo(DEFAULT_TIMEZONE)


def test_week_bounds_are_local_to_the_zone() -> None:
    """The same local week opens at a different UTC instant per zone — the whole point of #8."""
    monday = date(2026, 7, 6)
    ams_start, _ = _week_bounds(monday, ZoneInfo("Europe/Amsterdam"))
    nyc_start, _ = _week_bounds(monday, ZoneInfo("America/New_York"))
    # Amsterdam midnight (CEST, +02) is 22:00 UTC the day before; New York (EDT, −04) is 04:00.
    assert ams_start == datetime(2026, 7, 5, 22, tzinfo=UTC)
    assert nyc_start == datetime(2026, 7, 6, 4, tzinfo=UTC)


def test_previous_week_start_honours_an_explicit_day() -> None:
    # tz only steers "now"; an explicit day is zone-independent.
    assert previous_week_start(date(2026, 7, 10)) == date(2026, 6, 29)
    assert previous_week_start(date(2026, 7, 10), ZoneInfo("Pacific/Auckland")) == date(2026, 6, 29)


async def test_tenant_meta_exposes_and_updates_timezone(client_for) -> None:
    t = await make_tenant("tz-meta")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        # Seeded orgs adopt the instance default.
        public = (await c.get("/api/v1/meta/tenant")).json()
        assert public["timezone"] == DEFAULT_TIMEZONE

        # Members may not change org branding/timezone.
        assert (
            await c.patch(
                "/api/v1/meta/tenant",
                json={"timezone": "America/New_York"},
                headers=member_headers,
            )
        ).status_code == 403

        # A manager may; a bad zone is rejected; the change is public.
        assert (
            await c.patch(
                "/api/v1/meta/tenant",
                json={"timezone": "Mars/Olympus_Mons"},
                headers=owner_headers,
            )
        ).status_code == 422
        updated = await c.patch(
            "/api/v1/meta/tenant",
            json={"timezone": "America/New_York"},
            headers=owner_headers,
        )
        assert updated.status_code == 200
        assert updated.json()["timezone"] == "America/New_York"
        assert (await c.get("/api/v1/meta/tenant")).json()["timezone"] == "America/New_York"


async def test_org_zoneinfo_reads_the_configured_zone() -> None:
    t = await make_tenant("tz-job")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        s = await session.scalar(select(OrgSettings).where(OrgSettings.org_id == t.org.id))
        s.timezone = "America/New_York"
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert await org_zoneinfo(session, t.org.id) == ZoneInfo("America/New_York")
