"""Weekly work schedules: the hour arithmetic, its validation, and the API (#46).

The arithmetic gets unit tests because every leave hour in the product is derived from it and
a wrong 8.0 looks exactly like a right one. The API gets a tenant-isolation test because
``leave_settings`` is a new org-scoped surface (Golden Rule 1).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.auth.models import User
from app.modules.leave import schedule as sched
from tests.conftest import auth_cookie, make_tenant

# 08:30–17:00 with a 12:30–13:00 lunch, plus a 10:15–10:30 coffee break on Tuesday.
_TWO_BREAK_DAY = {
    "start": "08:30",
    "end": "17:00",
    "breaks": [{"start": "10:15", "end": "10:30"}, {"start": "12:30", "end": "13:00"}],
}


def _minutes(hhmm: str) -> int:
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


# --- the arithmetic ---------------------------------------------------------- #
def test_default_schedule_is_eight_hours_a_day_and_forty_a_week() -> None:
    schedule = sched.default_schedule()
    assert sched.week_hours(schedule) == Decimal("40.00")
    assert sched.average_day_hours(schedule) == Decimal("8.00")
    assert sched.working_day_count(schedule) == 5
    assert sched.day_minutes(schedule.day(0)) == 480  # Monday: 8.5 h minus a 0.5 h lunch
    assert sched.day_minutes(schedule.day(5)) == 0  # Saturday is not a working day


def test_average_day_is_the_scheduled_day_not_the_week_over_five() -> None:
    """A three-day week is still made of 8-hour days (#46). ``24 / 5`` is 4.8 and a lie."""
    schedule = sched.WorkSchedule.model_validate(
        {
            **{day: dict(sched.DEFAULT_SCHEDULE_JSON["mon"]) for day in ("mon", "tue", "wed")},
            "thu": None,
            "fri": None,
        }
    )
    assert sched.week_hours(schedule) == Decimal("24.00")
    assert sched.average_day_hours(schedule) == Decimal("8.00")


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        (None, 465),  # whole day: 8.5 h − 0:15 − 0:30 = 7.75 h
        (("15:00", "17:00"), 120),  # spans neither break
        (("10:00", "11:00"), 45),  # spans the morning break only: 1.0 − 0.25
        (("09:00", "14:00"), 255),  # spans both: 5.0 − 0.25 − 0.5 = 4.25 h
        (("12:45", "13:15"), 15),  # starts inside lunch
        (("12:30", "13:00"), 0),  # entirely inside a break
        (("08:00", "09:00"), 30),  # clamped to the 08:30 start, not rejected
        (("17:30", "18:00"), 0),  # after the working day
    ],
)
def test_day_minutes_subtracts_each_break_it_overlaps(
    window: tuple[str, str] | None, expected: int
) -> None:
    day = sched.WorkDay.model_validate(_TWO_BREAK_DAY)
    bounds = (_minutes(window[0]), _minutes(window[1])) if window else None
    assert sched.day_minutes(day, bounds) == expected


def test_breaks_are_sorted_on_the_way_in() -> None:
    day = sched.WorkDay.model_validate(
        {
            "start": "08:30",
            "end": "17:00",
            "breaks": [{"start": "12:30", "end": "13:00"}, {"start": "10:15", "end": "10:30"}],
        }
    )
    assert [b.start.isoformat("minutes") for b in day.breaks] == ["10:15", "12:30"]


def test_a_day_with_no_breaks_is_valid() -> None:
    day = sched.WorkDay.model_validate({"start": "09:00", "end": "13:00", "breaks": []})
    assert sched.day_minutes(day) == 240


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"start": "17:00", "end": "08:30"}, "errors.leave_schedule_day_invalid"),
        (
            {"start": "09:00", "end": "17:00", "breaks": [{"start": "08:00", "end": "08:30"}]},
            "errors.leave_schedule_break_outside",
        ),
        (
            {"start": "09:00", "end": "17:00", "breaks": [{"start": "16:30", "end": "17:30"}]},
            "errors.leave_schedule_break_outside",
        ),
        (
            {
                "start": "09:00",
                "end": "17:00",
                "breaks": [
                    {"start": "12:00", "end": "13:00"},
                    {"start": "12:30", "end": "13:30"},
                ],
            },
            "errors.leave_schedule_breaks_overlap",
        ),
        (
            {"start": "12:00", "end": "13:00", "breaks": [{"start": "12:00", "end": "13:00"}]},
            "errors.leave_schedule_day_empty",
        ),
        ({"start": "09:00", "end": "17:00", "breaks": [{"start": "13:00", "end": "12:00"}]},
         "errors.leave_schedule_break_invalid"),
    ],
)
def test_invalid_days_are_rejected_with_an_i18n_key(payload: dict, message: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        sched.WorkDay.model_validate(payload)
    assert message in str(excinfo.value)


def test_a_week_with_no_working_days_is_rejected() -> None:
    with pytest.raises(ValidationError):
        sched.WorkSchedule.model_validate(dict.fromkeys(sched.WEEKDAYS))


def test_round_trip_through_jsonb_is_hh_mm() -> None:
    dumped = sched.dump(sched.default_schedule())
    assert dumped["mon"] == {
        "start": "08:30",
        "end": "17:00",
        "breaks": [{"start": "12:30", "end": "13:00"}],
    }
    assert dumped["sat"] is None
    assert sched.week_hours(sched.parse(dumped)) == Decimal("40.00")


# --- the API ----------------------------------------------------------------- #
async def _invite_member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Member", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def test_profile_returns_the_effective_schedule(client_for) -> None:
    """The browser never merges the default itself — two clients would disagree (#46)."""
    tenant = await make_tenant("leave-sched-effective")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        res = await client.get("/api/v1/leave/profile", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["inherited"] is True
        assert body["hours_per_week"] == "40.00"
        assert body["hours_per_day"] == "8.00"
        assert body["schedule"]["mon"]["breaks"] == [{"start": "12:30", "end": "13:00"}]
        assert body["schedule"]["sun"] is None


async def test_saving_a_schedule_derives_hours_per_week(client_for) -> None:
    tenant = await make_tenant("leave-sched-derive")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        member = await _invite_member(client, headers, "part@example.com")
        three_days = {
            **{day: dict(sched.DEFAULT_SCHEDULE_JSON["mon"]) for day in ("mon", "tue", "wed")},
            "thu": None,
            "fri": None,
            "sat": None,
            "sun": None,
        }
        res = await client.put(
            f"/api/v1/leave/profiles/{member.id}",
            # `hours_per_week` is posted *and ignored*: the schedule is the input (#46).
            json={"hours_per_week": 40, "schedule": three_days},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["hours_per_week"] == "24.00"
        assert res.json()["hours_per_day"] == "8.00"

        # The employee reads back their own schedule, no longer inherited.
        mine = await client.get("/api/v1/leave/profile", headers=await auth_cookie(member))
        assert mine.json()["inherited"] is False
        assert mine.json()["schedule"]["thu"] is None


async def test_clearing_a_schedule_falls_back_to_the_org_default(client_for) -> None:
    tenant = await make_tenant("leave-sched-clear")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        member = await _invite_member(client, headers, "back@example.com")
        one_day = {**dict.fromkeys(sched.WEEKDAYS), "mon": dict(sched.DEFAULT_SCHEDULE_JSON["mon"])}
        await client.put(
            f"/api/v1/leave/profiles/{member.id}", json={"schedule": one_day}, headers=headers
        )
        res = await client.put(
            f"/api/v1/leave/profiles/{member.id}", json={"schedule": None}, headers=headers
        )
        assert res.status_code == 200, res.text
        assert res.json()["schedule"] is None
        assert res.json()["hours_per_week"] == "40.00"


async def test_hours_per_week_stays_authoritative_without_a_schedule(client_for) -> None:
    """A pre-#46 part-timer on 32 h must not be regranted the 40 h default (#46)."""
    tenant = await make_tenant("leave-sched-legacy")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        member = await _invite_member(client, headers, "legacy@example.com")
        # The old `web` container's payload: bare contract hours, no schedule.
        res = await client.put(
            f"/api/v1/leave/profiles/{member.id}", json={"hours_per_week": 32}, headers=headers
        )
        assert res.status_code == 200
        assert res.json()["hours_per_week"] == "32.00"
        assert res.json()["schedule"] is None

        mine = await client.get("/api/v1/leave/profile", headers=await auth_cookie(member))
        assert mine.json()["hours_per_week"] == "32.00"  # not 40
        assert mine.json()["inherited"] is True  # …but the *shape* is the org's

        # Once a schedule exists it wins, and a stale client's hours_per_week is ignored.
        one_day = {**dict.fromkeys(sched.WEEKDAYS), "mon": dict(sched.DEFAULT_SCHEDULE_JSON["mon"])}
        await client.put(
            f"/api/v1/leave/profiles/{member.id}", json={"schedule": one_day}, headers=headers
        )
        stale = await client.put(
            f"/api/v1/leave/profiles/{member.id}", json={"hours_per_week": 32}, headers=headers
        )
        assert stale.json()["hours_per_week"] == "8.00"


async def test_org_default_schedule_round_trip(client_for) -> None:
    tenant = await make_tenant("leave-sched-settings")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        assert (await client.get("/api/v1/leave/settings", headers=headers)).json()[
            "default_schedule"
        ]["mon"]["start"] == "08:30"

        nine_to_five = {
            **{
                day: {"start": "09:00", "end": "17:00", "breaks": []}
                for day in ("mon", "tue", "wed", "thu", "fri")
            },
            "sat": None,
            "sun": None,
        }
        res = await client.put(
            "/api/v1/leave/settings", json={"default_schedule": nine_to_five}, headers=headers
        )
        assert res.status_code == 200, res.text
        # Everyone without their own schedule now works a 40 h week of uninterrupted 8 h days.
        profile = await client.get("/api/v1/leave/profile", headers=headers)
        assert profile.json()["schedule"]["mon"]["breaks"] == []
        assert profile.json()["hours_per_week"] == "40.00"


async def test_invalid_schedule_is_rejected_with_its_own_message_key(client_for) -> None:
    tenant = await make_tenant("leave-sched-invalid")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        broken = {
            **dict.fromkeys(sched.WEEKDAYS),
            "mon": {
                "start": "09:00",
                "end": "17:00",
                "breaks": [{"start": "08:00", "end": "08:30"}],
            },
        }
        res = await client.put(
            "/api/v1/leave/settings", json={"default_schedule": broken}, headers=headers
        )
        assert res.status_code == 422
        assert "errors.leave_schedule_break_outside" in res.text


async def test_members_cannot_manage_schedules(client_for) -> None:
    tenant = await make_tenant("leave-sched-rbac")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        member = await _invite_member(client, headers, "nosy@example.com")
        headers_m = await auth_cookie(member)
        assert (await client.get("/api/v1/leave/settings", headers=headers_m)).status_code == 403
        assert (await client.get("/api/v1/leave/profiles", headers=headers_m)).status_code == 403
        res = await client.put(
            f"/api/v1/leave/profiles/{member.id}", json={"hours_per_week": 8}, headers=headers_m
        )
        assert res.status_code == 403
        # …but they always read their own.
        assert (await client.get("/api/v1/leave/profile", headers=headers_m)).status_code == 200


async def test_schedule_tenant_isolation(client_for) -> None:
    """Org A's default schedule and profiles are invisible to org B (Golden Rule 1)."""
    a = await make_tenant("leave-sched-a")
    b = await make_tenant("leave-sched-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)

    four_hours = {
        **dict.fromkeys(sched.WEEKDAYS),
        "mon": {"start": "09:00", "end": "13:00", "breaks": []},
    }
    async with client_for(a.host) as client:
        saved = await client.put(
            "/api/v1/leave/settings", json={"default_schedule": four_hours}, headers=a_headers
        )
        assert saved.status_code == 200

    async with client_for(b.host) as client:
        # B still sees the shipped default, not A's four-hour Monday.
        settings = await client.get("/api/v1/leave/settings", headers=b_headers)
        assert settings.json()["default_schedule"]["mon"]["end"] == "17:00"
        assert settings.json()["default_schedule"]["tue"] is not None
        # A's user is not a member here, so their profile cannot be written either.
        res = await client.put(
            f"/api/v1/leave/profiles/{a.user.id}", json={"hours_per_week": 8}, headers=b_headers
        )
        assert res.status_code == 404

    # A's own settings survived B's read untouched.
    async with client_for(a.host) as client:
        mine = await client.get("/api/v1/leave/settings", headers=a_headers)
        assert mine.json()["default_schedule"]["tue"] is None
