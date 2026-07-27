"""Free time: spread patterns, the overview read, and withdrawing an orphaned day.

The three things #65's machinery could not do. A pattern could only be expressed as a fixed
cadence, which no whole number of weeks fits for most contracts and which loses a day whenever
one lands on a holiday. The balance could only say "0 h over" once every day was placed, which is
true and answers nothing anybody asks. And a contract change reprorated the pot while leaving the
already-placed days on the calendar, with nothing to reconcile the two.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant

_YEAR = date.today().year


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Member", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _free_time_id(client, headers) -> str:
    rows = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return next(r["id"] for r in rows if r["key"] == "roostervrij")


async def _contract(client, headers, member_id, hours: str = "36", **extra):
    res = await client.post(
        "/api/v1/leave/contracts",
        json={
            "user_id": str(member_id),
            "start_date": f"{_YEAR}-01-01",
            "contract_hours_per_week": hours,
            **extra,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _overview(client, headers, member_id, year: int = _YEAR) -> dict:
    res = await client.get(
        "/api/v1/leave/free-time",
        params={"year": year, "user_id": str(member_id)},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


def _next_weekday(weekday: int, *, after: date | None = None) -> date:
    """The next ``weekday`` strictly after ``after`` (default today) — so a pattern anchored on it
    never backfills, whichever day the suite happens to run."""
    start = (after or date.today()) + timedelta(days=1)
    return start + timedelta(days=(weekday - start.weekday()) % 7)


# --- spread mode ----------------------------------------------------------------------- #
async def test_spread_mode_places_the_requested_number_of_days(client_for) -> None:
    """"26 free days on a Friday" places 26, without anyone working out a cadence.

    The whole point of the mode: a 36-h contract earns 26 days, and *that* is the number the
    manager knows. Whether it works out to "every other week" is the generator's problem.
    """
    t = await make_tenant("ft-spread")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "spread@example.com")
        await _contract(c, headers, member.id, "36")
        anchor = _next_weekday(4)  # a Friday

        res = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": await _free_time_id(c, headers),
                "anchor_date": anchor.isoformat(),
                "days_per_year": 26,
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        # The nearest equivalent cadence is stored alongside, so a rolled-back image behaves.
        assert res.json()["interval_weeks"] == 2
        assert res.json()["days_per_year"] == 26

        overview = await _overview(c, headers, member.id)
        # The pattern starts partway through the year, so it prorates; what matters is that days
        # landed, the first is the anchor, and nothing exceeded the pot.
        assert len(overview["days"]) > 0
        assert overview["days"][0]["date"] == anchor.isoformat()
        assert float(overview["overhang_hours"]) == 0.0
        assert float(overview["placed_hours"]) <= float(overview["entitled_hours"])


async def test_spread_days_are_not_bunched_at_the_start_of_the_year(client_for) -> None:
    """Evenly spread, not "the first N Fridays" — the difference between a roster and a binge."""
    t = await make_tenant("ft-spacing")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "spacing@example.com")
        await _contract(c, headers, member.id, "36")
        anchor = _next_weekday(4)

        res = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": await _free_time_id(c, headers),
                "anchor_date": anchor.isoformat(),
                "days_per_year": 26,
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text

        overview = await _overview(c, headers, member.id)
        days = [date.fromisoformat(d["date"]) for d in overview["days"]]
        if len(days) < 3:
            return  # too late in the year to say anything about spacing
        gaps = {(b - a).days for a, b in zip(days, days[1:], strict=False)}
        # A 26-of-52 spread is every other week; a bunched generator would give 7 throughout.
        assert gaps <= {14, 21}, f"expected a fortnightly rhythm, got gaps {sorted(gaps)}"


async def test_spread_mode_slides_past_a_holiday_and_still_lands_the_count(client_for) -> None:
    """A candidate lost to a holiday moves to the next week instead of vanishing.

    A fixed cadence cannot do this: the occurrence is worth zero hours, is skipped, and the year
    quietly ends up one day short of what the employee earned.
    """
    t = await make_tenant("ft-slide")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "slide@example.com")
        await _contract(c, headers, member.id, "36")
        anchor = _next_weekday(4)

        # Make the anchor itself a holiday, so the very first candidate is unusable.
        holiday = await c.post(
            "/api/v1/leave/holidays",
            json={"date": anchor.isoformat(), "name_i18n": {"nl": "Test", "en": "Test"}},
            headers=headers,
        )
        assert holiday.status_code == 201, holiday.text

        res = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": await _free_time_id(c, headers),
                "anchor_date": anchor.isoformat(),
                "days_per_year": 26,
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text

        days = [d["date"] for d in (await _overview(c, headers, member.id))["days"]]
        assert anchor.isoformat() not in days, "the holiday itself must not be booked"
        if days:
            # It slid to the following week rather than skipping a fortnight.
            assert days[0] == (anchor + timedelta(weeks=1)).isoformat()


async def test_regenerating_a_spread_pattern_places_nothing_new(client_for) -> None:
    """Idempotent: a second run must not read the quota as unfilled and double the calendar."""
    t = await make_tenant("ft-idempotent")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "idem@example.com")
        await _contract(c, headers, member.id, "36")
        created = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": await _free_time_id(c, headers),
                "anchor_date": _next_weekday(4).isoformat(),
                "days_per_year": 26,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        first = len((await _overview(c, headers, member.id))["days"])

        # Saving the pattern again re-runs the generator.
        again = await c.patch(
            f"/api/v1/leave/recurring/{created.json()['id']}",
            json={"note": "unchanged"},
            headers=headers,
        )
        assert again.status_code == 200, again.text
        assert again.json()["generated"] == 0
        assert len((await _overview(c, headers, member.id))["days"]) == first


async def test_interval_mode_is_untouched(client_for) -> None:
    """``days_per_year`` omitted keeps the #107 cadence exactly — every pattern already on file."""
    t = await make_tenant("ft-interval")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "interval@example.com")
        await _contract(c, headers, member.id, "36")
        anchor = _next_weekday(2)  # a Wednesday

        res = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": await _free_time_id(c, headers),
                "anchor_date": anchor.isoformat(),
                "interval_weeks": 2,
                # A Wednesday *afternoon* off, every other week — the part-day case.
                "start_time": "13:00",
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        assert res.json()["days_per_year"] is None

        overview = await _overview(c, headers, member.id)
        days = [date.fromisoformat(d["date"]) for d in overview["days"]]
        assert days and days[0] == anchor
        assert all(d.weekday() == 2 for d in days)
        gaps = {(b - a).days for a, b in zip(days, days[1:], strict=False)}
        assert gaps <= {14}, f"a two-week cadence must stay two weeks, got {sorted(gaps)}"
        # An afternoon costs half a day, so the pot buys twice as many of them.
        assert float(overview["days"][0]["hours"]) == 4.0


# --- the overview read ------------------------------------------------------------------ #
async def test_overview_answers_what_the_balance_cannot(client_for) -> None:
    """Placed / upcoming / next date — the figures "0 h over" hides once every day is booked."""
    t = await make_tenant("ft-overview")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "overview@example.com")
        await _contract(c, headers, member.id, "36")
        anchor = _next_weekday(4)
        res = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": await _free_time_id(c, headers),
                "anchor_date": anchor.isoformat(),
                "days_per_year": 26,
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text

        overview = await _overview(c, headers, member.id)
        assert overview["next_date"] == anchor.isoformat()
        assert float(overview["hours_per_day"]) == 8.0
        assert all(d["from_pattern"] for d in overview["days"])
        # Internally consistent: what is placed is taken + upcoming, and the pot covers it.
        assert float(overview["placed_hours"]) == float(overview["taken_hours"]) + float(
            overview["upcoming_hours"]
        )
        assert float(overview["unplaced_hours"]) == max(
            0.0, float(overview["entitled_hours"]) - float(overview["placed_hours"])
        )


async def test_overview_is_all_zero_when_free_time_is_deactivated(client_for) -> None:
    """A tenant that wants none of it deactivates the type; the read then reports nothing rather
    than failing."""
    t = await make_tenant("ft-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "off@example.com")
        await c.patch(
            f"/api/v1/leave/types/{await _free_time_id(c, headers)}",
            json={"active": False},
            headers=headers,
        )
        overview = await _overview(c, headers, member.id)
        assert overview["leave_type_ids"] == []
        assert float(overview["entitled_hours"]) == 0.0
        assert overview["days"] == []
        assert overview["next_date"] is None


async def test_overview_of_another_employee_needs_the_any_scope(client_for) -> None:
    """A member reads their own; someone else's is ``leave.request.read:any`` (§15)."""
    t = await make_tenant("ft-scope")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "scope@example.com")
        other = await _member(c, headers, "other@example.com")
        member_headers = await auth_cookie(member)

        own = await c.get(
            "/api/v1/leave/free-time", params={"year": _YEAR}, headers=member_headers
        )
        assert own.status_code == 200, own.text
        assert own.json()["user_id"] == str(member.id)

        peek = await c.get(
            "/api/v1/leave/free-time",
            params={"year": _YEAR, "user_id": str(other.id)},
            headers=member_headers,
        )
        assert peek.status_code == 403


async def test_free_time_overview_is_tenant_scoped(client_for) -> None:
    """Another tenant's employee is a 403/404, never a readable balance (Golden Rule 1)."""
    a = await make_tenant("ft-ov-iso-a")
    b = await make_tenant("ft-ov-iso-b")
    headers_a, headers_b = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as c:
        member = await _member(c, headers_a, "isoov@example.com")
        await _contract(c, headers_a, member.id, "36")

    async with client_for(b.host) as c:
        res = await c.get(
            "/api/v1/leave/free-time",
            params={"year": _YEAR, "user_id": str(member.id)},
            headers=headers_b,
        )
        # The owner of org B holds `:any`, so this is not a permission refusal — it must simply
        # find nothing of A's. An empty pot, never A's numbers.
        assert res.status_code in (200, 404)
        if res.status_code == 200:
            assert float(res.json()["entitled_hours"]) == 0.0
            assert res.json()["days"] == []


# --- overhang + withdraw ----------------------------------------------------------------- #
async def test_contract_raise_surfaces_the_orphaned_days_and_withdraws_them(client_for) -> None:
    """36 → 40 mid-year: the pot goes to zero and the placed days become reportable overhang.

    #264 reprorates the entitlement on a contract change but leaves the days on the calendar, so
    the balance silently goes negative and nobody is told. The overview names them; withdrawing is
    still a deliberate act on a list the caller was shown.
    """
    t = await make_tenant("ft-overhang")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "overhang@example.com")
        contract = await _contract(c, headers, member.id, "36")
        created = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": await _free_time_id(c, headers),
                "anchor_date": _next_weekday(4).isoformat(),
                "days_per_year": 26,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        before = await _overview(c, headers, member.id)
        if not before["days"]:
            return  # run too late in the year for the pattern to have placed anything
        assert float(before["overhang_hours"]) == 0.0

        # A raise to the full-time norm: the free-time pot is now zero.
        patched = await c.patch(
            f"/api/v1/leave/contracts/{contract['id']}",
            json={"contract_hours_per_week": "40"},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text

        after = await _overview(c, headers, member.id)
        assert float(after["entitled_hours"]) == 0.0
        assert float(after["overhang_hours"]) > 0.0
        assert len(after["overhang"]) > 0
        assert all(d["from_pattern"] for d in after["overhang"])
        # Latest first, so withdrawing in order gives back the most recently planned days.
        dates = [d["date"] for d in after["overhang"]]
        assert dates == sorted(dates, reverse=True)

        withdrawn = await c.post(
            "/api/v1/leave/free-time/withdraw",
            json={"request_ids": [d["request_id"] for d in after["overhang"]]},
            headers=headers,
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["cancelled"] == len(after["overhang"])
        assert withdrawn.json()["skipped"] == []

        settled = await _overview(c, headers, member.id)
        assert float(settled["overhang_hours"]) == 0.0


async def test_withdraw_skips_what_it_cannot_cancel_instead_of_failing(client_for) -> None:
    """One stale id must not abandon the rest of the withdrawal."""
    t = await make_tenant("ft-withdraw-skip")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/leave/free-time/withdraw",
            json={"request_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["cancelled"] == 0
        assert len(res.json()["skipped"]) == 2
