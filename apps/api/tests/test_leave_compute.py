"""The server computes the hours (#48): schedule ∖ (weekend, holiday, break), part days at each end.

Every row of both tables in issue #48 is asserted here, because this is the arithmetic every
leave balance in the product is made of, and a wrong 7,0 looks exactly like a right one.

The schedule under test is the shipped default — ``08:30–17:00`` with a ``12:30–13:00`` lunch —
so a whole day is 8.0 h. Some cases add a second, morning break at ``10:15–10:30``.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant

# A Thursday and the Friday after it, far from any Dutch holiday.
THU = date(2026, 6, 11)
FRI = date(2026, 6, 12)
SAT = date(2026, 6, 13)
assert THU.weekday() == 3 and FRI.weekday() == 4 and SAT.weekday() == 5

_DAY = {"start": "08:30", "end": "17:00", "breaks": [{"start": "12:30", "end": "13:00"}]}
_TWO_BREAKS = {
    "start": "08:30",
    "end": "17:00",
    "breaks": [{"start": "10:15", "end": "10:30"}, {"start": "12:30", "end": "13:00"}],
}


def _week(**days: dict | None) -> dict:
    base = {key: None for key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    return {**base, **days}


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Employee", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _preview(client, headers, **body) -> dict:
    res = await client.post("/api/v1/leave/requests/preview", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _holiday(client, headers, day: date, name: str = "Testdag") -> None:
    res = await client.post(
        "/api/v1/leave/holidays",
        json={"date": day.isoformat(), "name_i18n": {"nl": name, "en": name}},
        headers=headers,
    )
    assert res.status_code == 201, res.text


async def _type_id(client, headers, key: str = "special") -> str:
    """`special` tracks no balance, so these tests measure the *hours*, not the entitlement."""
    types = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return next(t for t in types if t["key"] == key)["id"]


# --- the worked example the epic exists for ------------------------------------ #
async def test_thursday_1500_to_friday_1400_is_seven_hours(client_for) -> None:
    """`Thu 15:00 → Fri 14:00` on the default schedule: 2.0 + 5.0 = 7.0 (issue #57)."""
    tenant = await make_tenant("leave-compute-7")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        result = await _preview(
            client,
            headers,
            start_date=THU.isoformat(),
            start_time="15:00",
            end_date=FRI.isoformat(),
            end_time="14:00",
        )
        assert result["hours"] == "7.00"
        assert result["days"] == "0.88"  # 7 / 8, the average scheduled working day
        assert [(d["date"], d["hours"], d["reason"]) for d in result["breakdown"]] == [
            (THU.isoformat(), "2.00", None),  # 15:00–17:00, no break in the window
            (FRI.isoformat(), "5.00", None),  # 08:30–14:00 minus 12:30–13:00
        ]


async def test_same_span_costs_two_hours_when_the_friday_is_a_holiday(client_for) -> None:
    tenant = await make_tenant("leave-compute-holiday")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _holiday(client, headers, FRI, "Goede Vrijdag")
        result = await _preview(
            client,
            headers,
            start_date=THU.isoformat(),
            start_time="15:00",
            end_date=FRI.isoformat(),
            end_time="14:00",
        )
        assert result["hours"] == "2.00"
        assert result["breakdown"][1] == {
            "date": FRI.isoformat(),
            "hours": "0.00",
            "reason": "holiday",
        }


async def test_same_span_costs_two_hours_when_the_employee_does_not_work_fridays(
    client_for,
) -> None:
    tenant = await make_tenant("leave-compute-nofriday")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        employee = await _member(client, headers, "mon-thu@example.com")
        await client.put(
            f"/api/v1/leave/profiles/{employee.id}",
            json={"schedule": _week(mon=_DAY, tue=_DAY, wed=_DAY, thu=_DAY)},
            headers=headers,
        )
        result = await _preview(
            client,
            await auth_cookie(employee),
            start_date=THU.isoformat(),
            start_time="15:00",
            end_date=FRI.isoformat(),
            end_time="14:00",
        )
        assert result["hours"] == "2.00"
        assert result["breakdown"][1]["reason"] == "not_scheduled"


@pytest.mark.parametrize(
    ("start_time", "end_time", "expected", "reason"),
    [
        ("12:45", "13:15", "0.25", None),  # half in lunch, half after it
        ("12:30", "13:00", "0.00", "outside_hours"),  # entirely inside the break
        ("08:00", "17:30", "8.00", None),  # clamped at both ends, not rejected
        ("15:00", "17:00", "2.00", None),
    ],
)
async def test_same_day_part_days(
    client_for, start_time: str, end_time: str, expected: str, reason: str | None
) -> None:
    slug = f"leave-compute-{start_time}-{end_time}".replace(":", "")
    tenant = await make_tenant(slug)
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        result = await _preview(
            client,
            headers,
            start_date=THU.isoformat(),
            start_time=start_time,
            end_date=THU.isoformat(),
            end_time=end_time,
        )
        assert result["hours"] == expected
        assert result["breakdown"][0]["reason"] == reason


async def test_a_request_inside_the_break_is_rejected_not_stored(client_for) -> None:
    """`Thu 12:45 → Thu 13:15` is 0.25 h; `Thu 12:30 → 13:00` is 0.0 and must not be stored."""
    tenant = await make_tenant("leave-compute-inbreak")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        res = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "start_date": THU.isoformat(),
                "start_time": "12:30",
                "end_date": THU.isoformat(),
                "end_time": "13:00",
            },
            headers=headers,
        )
        assert res.status_code == 422
        assert "errors.leave_no_working_hours" in res.text


async def test_a_saturday_only_request_is_rejected(client_for) -> None:
    tenant = await make_tenant("leave-compute-saturday")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        res = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "start_date": SAT.isoformat(),
                "end_date": SAT.isoformat(),
            },
            headers=headers,
        )
        assert res.status_code == 422
        assert "errors.leave_no_working_hours" in res.text


async def test_a_whole_kerst_week_costs_twenty_four_hours_not_forty(client_for) -> None:
    """Mon → Fri with two holidays in it: 3 × 8.0 = 24.0, not 40.0."""
    tenant = await make_tenant("leave-compute-kerst")
    headers = await auth_cookie(tenant.user)
    monday = date(2026, 6, 15)
    assert monday.weekday() == 0
    async with client_for(tenant.host) as client:
        await _holiday(client, headers, monday + timedelta(days=3), "Eerste Kerstdag")
        await _holiday(client, headers, monday + timedelta(days=4), "Tweede Kerstdag")
        result = await _preview(
            client,
            headers,
            start_date=monday.isoformat(),
            end_date=(monday + timedelta(days=4)).isoformat(),
        )
        assert result["hours"] == "24.00"
        assert result["days"] == "3.00"
        reasons = [d["reason"] for d in result["breakdown"]]
        assert reasons == [None, None, None, "holiday", "holiday"]


# --- a day with two breaks ------------------------------------------------------ #
@pytest.mark.parametrize(
    ("start_time", "end_time", "expected"),
    [
        (None, None, "7.75"),  # whole day: 8.5 − 0.25 − 0.5
        ("10:00", "11:00", "0.75"),  # spans the morning break only
        ("09:00", "14:00", "4.25"),  # spans both
        ("15:00", "17:00", "2.00"),  # spans neither
    ],
)
async def test_two_breaks_on_one_day(
    client_for, start_time: str | None, end_time: str | None, expected: str
) -> None:
    slug = f"leave-2b-{start_time or 'all'}-{end_time or 'all'}".replace(":", "")
    tenant = await make_tenant(slug)
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await client.put(
            "/api/v1/leave/settings",
            json={
                "default_schedule": _week(
                    mon=_TWO_BREAKS, tue=_TWO_BREAKS, wed=_TWO_BREAKS,
                    thu=_TWO_BREAKS, fri=_TWO_BREAKS,
                )
            },
            headers=headers,
        )
        result = await _preview(
            client,
            headers,
            start_date=THU.isoformat(),
            start_time=start_time,
            end_date=THU.isoformat(),
            end_time=end_time,
        )
        assert result["hours"] == expected


# --- the server is the authority ------------------------------------------------ #
async def test_hours_are_not_accepted_from_the_client(client_for) -> None:
    """A request claiming 100 hours for one afternoon stores 2.0 (issue #48)."""
    tenant = await make_tenant("leave-compute-untrusted")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        res = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "start_date": THU.isoformat(),
                "start_time": "15:00",
                "end_date": THU.isoformat(),
                "end_time": "17:00",
                "hours": 100,  # ignored: `hours` is not a field of LeaveRequestCreate
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        assert res.json()["hours"] == "2.00"
        assert res.json()["hours_override"] is None


async def test_hours_are_recomputed_on_edit(client_for) -> None:
    """A request moved into a week with two holidays gets cheaper (issue #48)."""
    tenant = await make_tenant("leave-compute-recompute")
    headers = await auth_cookie(tenant.user)
    monday = date(2026, 6, 15)
    async with client_for(tenant.host) as client:
        created = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "start_date": monday.isoformat(),
                "end_date": (monday + timedelta(days=4)).isoformat(),
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["hours"] == "40.00"

        await _holiday(client, headers, monday + timedelta(days=3), "Eerste Kerstdag")
        await _holiday(client, headers, monday + timedelta(days=4), "Tweede Kerstdag")

        # Untouched: an approved request is never retroactively recalculated (#47).
        listed = await client.get("/api/v1/leave/requests", headers=headers)
        assert listed.json()["items"][0]["hours"] == "40.00"

        # Edited: recomputed against the calendar as it stands now.
        edited = await client.patch(
            f"/api/v1/leave/requests/{created.json()['id']}",
            json={"note": "moved"},
            headers=headers,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["hours"] == "24.00"


async def test_end_time_before_start_on_a_same_day_request_is_rejected(client_for) -> None:
    tenant = await make_tenant("leave-compute-backwards")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        res = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "start_date": THU.isoformat(),
                "start_time": "15:00",
                "end_date": THU.isoformat(),
                "end_time": "14:00",
            },
            headers=headers,
        )
        assert res.status_code == 422
        assert "errors.leave_end_time_before_start" in res.text
        # …but the same times across two days are an ordinary overnight span.
        ok = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "start_date": THU.isoformat(),
                "start_time": "15:00",
                "end_date": FRI.isoformat(),
                "end_time": "14:00",
            },
            headers=headers,
        )
        assert ok.status_code == 201, ok.text


# --- the manager override -------------------------------------------------------- #
async def test_manager_may_override_the_hours_and_it_is_attributed(client_for) -> None:
    """Four hours agreed for a Saturday: computed 0.0, stored 4.0, and we know who said so."""
    tenant = await make_tenant("leave-compute-override")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        employee = await _member(client, headers, "weekend@example.com")
        res = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "user_id": str(employee.id),
                "start_date": SAT.isoformat(),
                "end_date": SAT.isoformat(),
                "hours_override": 4,
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        assert res.json()["hours"] == "4.00"
        assert res.json()["hours_override"] == "4.00"
        assert res.json()["hours_override_by_user_id"] == str(tenant.user.id)

        # Clearing it returns the request to the computed hours — here, zero, so it is refused.
        cleared = await client.patch(
            f"/api/v1/leave/requests/{res.json()['id']}",
            json={"hours_override": None},
            headers=headers,
        )
        assert cleared.status_code == 422
        assert "errors.leave_no_working_hours" in cleared.text


async def test_a_member_cannot_override_their_own_hours(client_for) -> None:
    tenant = await make_tenant("leave-compute-override-rbac")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        employee = await _member(client, headers, "greedy@example.com")
        res = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "start_date": THU.isoformat(),
                "end_date": THU.isoformat(),
                "hours_override": 40,
            },
            headers=await auth_cookie(employee),
        )
        assert res.status_code == 403


async def test_a_member_may_edit_a_request_a_manager_overrode(client_for) -> None:
    """Leaving a stored override alone is not an approver's act; only setting one is."""
    tenant = await make_tenant("leave-compute-override-edit")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        employee = await _member(client, headers, "edits@example.com")
        created = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "user_id": str(employee.id),
                "start_date": SAT.isoformat(),
                "end_date": SAT.isoformat(),
                "hours_override": 4,
            },
            headers=headers,
        )
        assert created.status_code == 201
        edited = await client.patch(
            f"/api/v1/leave/requests/{created.json()['id']}",
            json={"note": "as agreed"},
            headers=await auth_cookie(employee),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["hours"] == "4.00"  # the override survives, unattributed to them
        assert edited.json()["hours_override_by_user_id"] == str(tenant.user.id)


# --- the timesheet breakdown ------------------------------------------------------ #
async def test_team_feed_carries_the_per_day_shape_not_an_even_spread(client_for) -> None:
    """2 h Thursday and 5 h Friday, never 3,5 / 3,5 (issue #48)."""
    tenant = await make_tenant("leave-compute-team")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        created = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "start_date": THU.isoformat(),
                "start_time": "15:00",
                "end_date": FRI.isoformat(),
                "end_time": "14:00",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text

        feed = await client.get(
            f"/api/v1/leave/team?date_from={THU}&date_to={FRI}", headers=headers
        )
        item = feed.json()[0]
        assert item["hours"] == "7.00"
        assert item["start_time"] == "15:00" and item["end_time"] == "14:00"
        assert [(d["date"], d["hours"]) for d in item["days"]] == [
            (THU.isoformat(), "2.00"),
            (FRI.isoformat(), "5.00"),
        ]
        assert sum(float(d["hours"]) for d in item["days"]) == 7.0


async def test_team_breakdown_never_contradicts_the_stored_total(client_for) -> None:
    """The shape comes from today's schedule; the total stays what was agreed (#47, #48)."""
    tenant = await make_tenant("leave-compute-drift")
    headers = await auth_cookie(tenant.user)
    monday = date(2026, 6, 15)
    async with client_for(tenant.host) as client:
        created = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": await _type_id(client, headers),
                "start_date": monday.isoformat(),
                "end_date": (monday + timedelta(days=4)).isoformat(),
            },
            headers=headers,
        )
        assert created.json()["hours"] == "40.00"

        # Two holidays appear *after* approval. The request keeps its 40 agreed hours.
        await _holiday(client, headers, monday + timedelta(days=3), "Eerste Kerstdag")
        await _holiday(client, headers, monday + timedelta(days=4), "Tweede Kerstdag")

        feed = await client.get(
            f"/api/v1/leave/team?date_from={monday}&date_to={monday + timedelta(days=4)}",
            headers=headers,
        )
        item = feed.json()[0]
        assert item["hours"] == "40.00"
        # …laid out over the three days that are still working days, summing to exactly 40.
        assert sum(float(d["hours"]) for d in item["days"]) == 40.0
        assert [d["reason"] for d in item["days"]] == [None, None, None, "holiday", "holiday"]
        assert [d["hours"] for d in item["days"][3:]] == ["0.00", "0.00"]


async def test_team_feed_costs_a_constant_number_of_queries(client_for, count_queries) -> None:
    """Shaping N requests must not cost N schedule reads (docs/PERFORMANCE.md).

    Profiles, the org default and the holidays in range load once each; the per-day arithmetic
    then runs in Python. Counting the statements is the only honest check — an N+1 and a grouped
    query return identical JSON, and the difference only shows up in production.
    """
    tenant = await make_tenant("leave-compute-queries")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        type_id = await _type_id(client, headers)
        employees = [await _member(client, headers, f"e{i}@example.com") for i in range(4)]
        for i, employee in enumerate(employees):
            day = date(2026, 6, 15) + timedelta(days=i)
            res = await client.post(
                "/api/v1/leave/requests",
                json={
                    "leave_type_id": type_id,
                    "user_id": str(employee.id),
                    "start_date": day.isoformat(),
                    "end_date": day.isoformat(),
                },
                headers=headers,
            )
            assert res.status_code == 201, res.text

        params = "date_from=2026-06-15&date_to=2026-06-19"
        one = f"/api/v1/leave/team?{params}&user_id={employees[0].id}"
        with count_queries() as one_request:
            await client.get(one, headers=headers)
        with count_queries() as four_requests:
            feed = await client.get(f"/api/v1/leave/team?{params}", headers=headers)

        assert len(feed.json()) == 4
        assert len(four_requests) == len(one_request)


# --- tenant isolation -------------------------------------------------------------- #
async def test_preview_cannot_cross_tenants(client_for) -> None:
    """A manager in org B may not preview a user of org A (Golden Rule 1)."""
    a = await make_tenant("leave-preview-a")
    b = await make_tenant("leave-preview-b")
    b_headers = await auth_cookie(b.user)
    async with client_for(b.host) as client:
        res = await client.post(
            "/api/v1/leave/requests/preview",
            json={
                "user_id": str(a.user.id),
                "start_date": THU.isoformat(),
                "end_date": THU.isoformat(),
            },
            headers=b_headers,
        )
        assert res.status_code == 404
        assert "errors.not_found" in res.text


async def test_preview_of_someone_else_needs_the_any_scope(client_for) -> None:
    tenant = await make_tenant("leave-preview-scope")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        employee = await _member(client, headers, "other@example.com")
        # A plain member holds `leave.request.read:own`, so they may not preview the owner's.
        res = await client.post(
            "/api/v1/leave/requests/preview",
            json={
                "user_id": str(tenant.user.id),
                "start_date": THU.isoformat(),
                "end_date": THU.isoformat(),
            },
            headers=await auth_cookie(employee),
        )
        assert res.status_code == 403
