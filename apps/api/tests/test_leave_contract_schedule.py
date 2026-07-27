"""The working week lives on the employment contract, and free time is a per-contract figure.

Two changes, one file, because they are the two halves of the same knot: what a person's week is,
and what that week earns them.

* **The week is a property of the period.** ``EmploymentContract.schedule`` is the authority for a
  date, so leave taken under last year's four-day contract is priced against *that* week even
  after the employee moved to five days. It falls back to ``LeaveProfile.schedule`` and then to
  the org default, which is what keeps every pre-existing employee computing exactly as before.
* **Free time is per contract.** ``free_time_hours_per_week`` is ``NULL`` to derive
  ``max(0, norm − contract)`` (the #282 rule) and set to say otherwise. ``0`` is the case that
  motivated the column: a 32-h part-timer working four 8-hour days already has Friday off, and
  the derived figure would grant them ~52 free days a year on top of it.
"""

from __future__ import annotations

import uuid
from datetime import date

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant

_YEAR = date.today().year

#: A four-day week: 8 h Monday through Thursday, Friday off. 32 h, and the whole point is that
#: nothing about it should earn free time when the contract is 32 h too.
_FOUR_DAY = {
    **{
        day: {"start": "08:30", "end": "17:00", "breaks": [{"start": "12:30", "end": "13:00"}]}
        for day in ("mon", "tue", "wed", "thu")
    },
    "fri": None,
    "sat": None,
    "sun": None,
}
#: Wednesdays off instead of Fridays — a different 32-h week, so a request priced against it
#: disagrees with ``_FOUR_DAY`` on exactly one weekday. That disagreement is the assertion.
_WED_OFF = {
    **{
        day: {"start": "08:30", "end": "17:00", "breaks": [{"start": "12:30", "end": "13:00"}]}
        for day in ("mon", "tue", "thu", "fri")
    },
    "wed": None,
    "sat": None,
    "sun": None,
}


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


async def _entitled(client, headers, member_id, type_id) -> float:
    rows = (
        await client.get(
            "/api/v1/leave/entitlements",
            params={"year": _YEAR, "user_id": str(member_id)},
            headers=headers,
        )
    ).json()
    match = next((e for e in rows if e["leave_type_id"] == type_id), None)
    return float(match["hours"]) if match else 0.0


def _first_weekday(weekday: int) -> date:
    """The first ``weekday`` of November this year — November has no Dutch holiday in it, so the
    day is worth its full scheduled hours (the reason ``conftest.leave_workday`` picks it)."""
    day = date(_YEAR, 11, 1)
    while day.weekday() != weekday:
        day = day.replace(day=day.day + 1)
    return day


# --- free time is per contract --------------------------------------------------------- #
async def test_derived_free_time_is_the_norm_shortfall(client_for) -> None:
    """``NULL`` keeps the #282 rule: a 36-h contract earns 4 h/week, ~26 days a year.

    The regression guard for every contract already on file — the migration backfills nothing, so
    this is what an untouched row must keep computing.
    """
    t = await make_tenant("ft-derived")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "derived@example.com")
        created = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "contract_hours_per_week": "36",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["free_time_hours_per_week"] is None
        # 40 − 36, resolved server-side so no client re-implements the fallback.
        assert float(created.json()["effective_free_time_per_week"]) == 4.0

        # 4 h/week over the year, rounded to the nearest half day (4 h) = 208 h = 26 days.
        assert await _entitled(c, headers, member.id, await _free_time_id(c, headers)) == 208.0


async def test_zero_free_time_stops_the_part_timer_double_grant(client_for) -> None:
    """A 32-h contract worked as four 8-hour days earns **nothing** when the contract says so.

    Derived, this employee would get ``(40 − 32) × 52 ≈ 416 h ≈ 52 free days`` on top of a roster
    that already gives them Friday off. Before this column the only escape was deactivating the
    leave type for the whole org, so an agency holding both arrangements could not be modelled.
    """
    t = await make_tenant("ft-zero")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "fourday@example.com")
        created = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "contract_hours_per_week": "32",
                "schedule": _FOUR_DAY,
                "free_time_hours_per_week": "0",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert float(created.json()["effective_free_time_per_week"]) == 0.0
        assert float(created.json()["scheduled_hours_per_week"]) == 32.0
        # No pot at all, not a zero row cluttering the balance.
        assert await _entitled(c, headers, member.id, await _free_time_id(c, headers)) == 0.0


async def test_explicit_free_time_beats_the_derived_figure(client_for) -> None:
    """An agreed 2 h/week is honoured where the derived shortfall would say 8."""
    t = await make_tenant("ft-explicit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "agreed@example.com")
        created = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "contract_hours_per_week": "32",
                "free_time_hours_per_week": "2",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert float(created.json()["effective_free_time_per_week"]) == 2.0
        # 2 h/week ≈ 104 h, rounded to the nearest half day of an 8-hour week.
        assert await _entitled(c, headers, member.id, await _free_time_id(c, headers)) == 104.0


async def test_changing_free_time_repricess_the_pot(client_for) -> None:
    """Editing the field re-derives the generated pot in the same transaction (#264's rule)."""
    t = await make_tenant("ft-recompute")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "recompute@example.com")
        created = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "contract_hours_per_week": "36",
            },
            headers=headers,
        )
        free_time = await _free_time_id(c, headers)
        assert await _entitled(c, headers, member.id, free_time) == 208.0

        patched = await c.patch(
            f"/api/v1/leave/contracts/{created.json()['id']}",
            json={"free_time_hours_per_week": "0"},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert await _entitled(c, headers, member.id, free_time) == 0.0


# --- the week lives on the contract ---------------------------------------------------- #
async def test_leave_is_priced_against_the_contract_covering_it(client_for) -> None:
    """Two periods, two different four-day weeks: each day is priced by the week in force.

    The point of moving the schedule onto the contract. Under the old single-schedule model both
    requests would be measured against whichever week was current *now*, so the earlier one would
    silently re-price when the employee's roster changed.
    """
    t = await make_tenant("ft-history")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "history@example.com")
        # Last year: Fridays off. From 1 January: Wednesdays off instead.
        for start, end, week in (
            (f"{_YEAR - 1}-01-01", f"{_YEAR - 1}-12-31", _FOUR_DAY),
            (f"{_YEAR}-01-01", None, _WED_OFF),
        ):
            res = await c.post(
                "/api/v1/leave/contracts",
                json={
                    "user_id": str(member.id),
                    "start_date": start,
                    "end_date": end,
                    "contract_hours_per_week": "32",
                    "schedule": week,
                    "free_time_hours_per_week": "0",
                },
                headers=headers,
            )
            assert res.status_code == 201, res.text

        # A Wednesday: worked last year (8 h), not worked now (0 h).
        wednesday = _first_weekday(2)
        last_year = wednesday.replace(year=_YEAR - 1)

        def preview(day: date):
            return c.post(
                "/api/v1/leave/requests/preview",
                json={
                    "user_id": str(member.id),
                    "leave_type_id": None,
                    "start_date": day.isoformat(),
                    "end_date": day.isoformat(),
                },
                headers=headers,
            )

        old = await preview(last_year)
        assert old.status_code == 200, old.text
        assert float(old.json()["hours"]) == 8.0, "last year's contract worked Wednesdays"

        now = await preview(wednesday)
        assert now.status_code == 200, now.text
        assert float(now.json()["hours"]) == 0.0, "this year's contract does not"


async def test_contract_without_a_week_falls_back_to_the_profile(client_for) -> None:
    """A contract carrying no schedule keeps resolving through profile → org default.

    That fallback is what lets every pre-existing contract upgrade untouched, and why the backfill
    deliberately skipped employees who inherit the org default.
    """
    t = await make_tenant("ft-fallback")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "fallback@example.com")
        res = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "contract_hours_per_week": "32",
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        assert res.json()["schedule"] is None
        # The org default 40 h week, unchanged from before this landed.
        assert float(res.json()["scheduled_hours_per_week"]) == 40.0

        # Give the *profile* a four-day week; the contract inherits it, so Friday goes to zero.
        saved = await c.put(
            f"/api/v1/leave/profiles/{member.id}",
            json={"schedule": _FOUR_DAY},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text

        friday = _first_weekday(4)
        res = await c.post(
            "/api/v1/leave/requests/preview",
            json={
                "user_id": str(member.id),
                "leave_type_id": None,
                "start_date": friday.isoformat(),
                "end_date": friday.isoformat(),
            },
            headers=headers,
        )
        assert float(res.json()["hours"]) == 0.0


async def test_saving_a_profile_week_reaches_the_running_contract(client_for) -> None:
    """Saving "this person's week" lands on the contract in force, not only on the profile.

    Without this, the pre-wizard schedule modal would write a field the resolver never reaches for
    anyone who has a contract with a week on it: saving would silently do nothing.
    """
    t = await make_tenant("ft-profile-write")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "write@example.com")
        created = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "contract_hours_per_week": "32",
                "schedule": _WED_OFF,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text

        saved = await c.put(
            f"/api/v1/leave/profiles/{member.id}",
            json={"schedule": _FOUR_DAY},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text

        rows = (
            await c.get(
                "/api/v1/leave/contracts",
                params={"user_id": str(member.id)},
                headers=headers,
            )
        ).json()
        assert rows[0]["schedule"]["fri"] is None, "the saved week reached the contract"
        assert rows[0]["schedule"]["wed"] is not None


async def test_ended_contract_keeps_its_own_week(client_for) -> None:
    """A period that is over is history: saving a new week must not rewrite it."""
    t = await make_tenant("ft-history-locked")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "locked@example.com")
        past = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR - 1}-01-01",
                "end_date": f"{_YEAR - 1}-12-31",
                "contract_hours_per_week": "32",
                "schedule": _WED_OFF,
            },
            headers=headers,
        )
        assert past.status_code == 201, past.text

        await c.put(
            f"/api/v1/leave/profiles/{member.id}",
            json={"schedule": _FOUR_DAY},
            headers=headers,
        )

        rows = (
            await c.get(
                "/api/v1/leave/contracts",
                params={"user_id": str(member.id)},
                headers=headers,
            )
        ).json()
        ended = next(r for r in rows if r["end_date"] == f"{_YEAR - 1}-12-31")
        assert ended["schedule"]["wed"] is None, "the ended period kept the week it ran under"


# --- tenant isolation ------------------------------------------------------------------ #
async def test_contract_free_time_is_tenant_scoped(client_for) -> None:
    """One tenant's contract is invisible and unpatchable from another (Golden Rule 1)."""
    a = await make_tenant("ft-iso-a")
    b = await make_tenant("ft-iso-b")
    headers_a, headers_b = await auth_cookie(a.user), await auth_cookie(b.user)

    async with client_for(a.host) as c:
        member = await _member(c, headers_a, "iso@example.com")
        created = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "contract_hours_per_week": "36",
                "free_time_hours_per_week": "0",
            },
            headers=headers_a,
        )
        assert created.status_code == 201, created.text
        contract_id = created.json()["id"]

    async with client_for(b.host) as c:
        listed = await c.get(
            "/api/v1/leave/contracts", params={"all_users": True}, headers=headers_b
        )
        assert listed.status_code == 200
        assert all(r["id"] != contract_id for r in listed.json())

        stolen = await c.patch(
            f"/api/v1/leave/contracts/{contract_id}",
            json={"free_time_hours_per_week": "40"},
            headers=headers_b,
        )
        assert stolen.status_code == 404
