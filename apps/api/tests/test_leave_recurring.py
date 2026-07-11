"""Recurring rostered free days / ADV patterns (#107).

A pattern ("every 2nd Friday from <date>") lays auto-approved free days onto the calendar the
moment it is saved, and a monthly cron rolls the horizon forward. Idempotent: an occurrence any
request row has spent — standing, moved or cancelled — is never re-placed, so an employee who
shifted a day never finds it silently back on the pattern date. Holidays and non-working days
are skipped, and a balance-tracked type never generates past its pot.
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


async def _types(client, headers) -> dict[str, dict]:
    rows = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return {t["key"]: t for t in rows}


def _next_weekday(base: date, weekday: int) -> date:
    """The first `weekday` (0=Mon) strictly after `base`."""
    days = (weekday - base.weekday() - 1) % 7 + 1
    return base + timedelta(days=days)


def _add_months(day: date, months: int) -> date:
    """Mirror of LeaveService._add_months (day-of-month clamped)."""
    from calendar import monthrange

    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


async def _generated(client, headers, user_id) -> list[dict]:
    rows = []
    for year in (_YEAR, _YEAR + 1):
        page = (
            await client.get(
                "/api/v1/leave/requests",
                params={"user_id": str(user_id), "year": year, "limit": 200},
                headers=headers,
            )
        ).json()
        rows += [r for r in page["items"] if r["recurring_day_id"] is not None]
    return sorted(rows, key=lambda r: r["start_date"])


async def test_pattern_places_days_to_the_horizon(client_for) -> None:
    t = await make_tenant("recurring-basic")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        # A 36-hour contract on a 40-hour schedule earns a real ADV pot.
        res = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "end_date": None,
                "contract_hours_per_week": "36",
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text

        anchor = _next_weekday(date.today() + timedelta(days=7), 4)  # a future Friday
        created = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": types["roostervrij"]["id"],
                "anchor_date": anchor.isoformat(),
                "interval_weeks": 2,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["generated"] > 0

        rows = await _generated(c, headers, member.id)
        assert len(rows) == created.json()["generated"]
        # Open-ended contract → the rolling look-ahead (default 12 months), not a fixed window.
        horizon = _add_months(date.today(), 12)
        holidays = set()
        for year in (_YEAR, _YEAR + 1):
            holidays |= {
                h["date"]
                for h in (
                    await c.get(
                        "/api/v1/leave/holidays", params={"year": year}, headers=headers
                    )
                ).json()
            }
        for row in rows:
            day = date.fromisoformat(row["start_date"])
            assert row["status"] == "approved"  # pre-approved, movable registrations
            assert row["start_date"] == row["end_date"]
            assert day.weekday() == 4  # the anchor's weekday
            assert anchor <= day <= horizon
            assert (day - anchor).days % 14 == 0  # the cadence
            assert row["start_date"] not in holidays  # a holiday costs no free day
            assert float(row["hours"]) == 8.0


async def test_generation_is_idempotent_and_respects_moves(client_for) -> None:
    t = await make_tenant("recurring-idem")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        mh = await auth_cookie(member)
        types = await _types(c, headers)
        await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "end_date": None,
                "contract_hours_per_week": "36",
            },
            headers=headers,
        )

        anchor = _next_weekday(date.today() + timedelta(days=7), 4)
        pattern = (
            await c.post(
                "/api/v1/leave/recurring",
                json={
                    "user_id": str(member.id),
                    "leave_type_id": types["roostervrij"]["id"],
                    "anchor_date": anchor.isoformat(),
                    "interval_weeks": 2,
                },
                headers=headers,
            )
        ).json()
        first = await _generated(c, headers, member.id)
        assert len(first) > 1

        # A no-op save regenerates nothing.
        again = await c.patch(
            f"/api/v1/leave/recurring/{pattern['id']}",
            json={"interval_weeks": 2},
            headers=headers,
        )
        assert again.status_code == 200, again.text
        assert again.json()["generated"] == 0

        # The member cancels one generated day (their own future auto-approve leave, #72)…
        dropped = first[0]
        res = await c.post(
            f"/api/v1/leave/requests/{dropped['id']}/cancel", headers=mh
        )
        assert res.status_code == 200, res.text

        # …and a regenerate does NOT put it back: the occurrence is spent.
        again = await c.patch(
            f"/api/v1/leave/recurring/{pattern['id']}",
            json={"interval_weeks": 2},
            headers=headers,
        )
        assert again.json()["generated"] == 0
        remaining = await _generated(c, headers, member.id)
        standing = [r for r in remaining if r["status"] == "approved"]
        assert dropped["start_date"] not in {r["start_date"] for r in standing}


async def test_generation_stops_at_the_balance(client_for) -> None:
    """The pattern must never hand out more free hours than the schedule gap earned (§14)."""
    t = await make_tenant("recurring-balance")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        # No contract → no ADV accrual; grant exactly two bookable days by hand, in both years
        # the horizon can touch, so the cap is the only variable whenever this runs.
        for year in (_YEAR, _YEAR + 1):
            res = await c.put(
                "/api/v1/leave/entitlements",
                json={
                    "user_id": str(member.id),
                    "leave_type_id": types["roostervrij"]["id"],
                    "year": year,
                    "hours": 16,
                },
                headers=headers,
            )
            assert res.status_code == 200, res.text

        # Weekly cadence, months of horizon — but only 16 h per year in the pot.
        anchor = _next_weekday(date.today() + timedelta(days=7), 2)  # a future Wednesday
        created = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": types["roostervrij"]["id"],
                "anchor_date": anchor.isoformat(),
                "interval_weeks": 1,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        rows = await _generated(c, headers, member.id)
        assert len(rows) > 0
        for year in (_YEAR, _YEAR + 1):
            booked = sum(
                float(r["hours"]) for r in rows if r["start_date"].startswith(str(year))
            )
            assert booked <= 16.0


async def test_fixed_term_contract_is_filled_to_its_end_date(client_for) -> None:
    """The horizon *is* the contract term when an end date is entered — the whole term, and
    never a day past it: a free day after employment ends is meaningless."""
    t = await make_tenant("recurring-term")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        anchor = _next_weekday(date.today() + timedelta(days=7), 4)
        end = anchor + timedelta(weeks=5)  # room for the anchor + two biweekly repeats
        res = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "end_date": end.isoformat(),
                "contract_hours_per_week": "36",
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text

        created = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": types["roostervrij"]["id"],
                "anchor_date": anchor.isoformat(),
                "interval_weeks": 2,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        rows = await _generated(c, headers, member.id)
        assert len(rows) > 0
        assert all(date.fromisoformat(r["start_date"]) <= end for r in rows)


async def test_ended_contract_generates_nothing(client_for) -> None:
    """A departed employee's pattern is inert: the horizon lies behind today."""
    t = await make_tenant("recurring-departed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        res = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR - 1}-01-01",
                "end_date": (date.today() - timedelta(days=30)).isoformat(),
                "contract_hours_per_week": "36",
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        created = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": types["roostervrij"]["id"],
                "anchor_date": f"{_YEAR - 1}-01-08",
                "interval_weeks": 1,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["generated"] == 0
        assert await _generated(c, headers, member.id) == []


async def test_horizon_setting_bounds_open_ended_generation(client_for) -> None:
    """`leave_settings.recurring_horizon_months` governs the open-ended look-ahead."""
    t = await make_tenant("recurring-setting")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "end_date": None,
                "contract_hours_per_week": "36",
            },
            headers=headers,
        )
        res = await c.put(
            "/api/v1/leave/settings", json={"recurring_horizon_months": 1}, headers=headers
        )
        assert res.status_code == 200, res.text
        assert res.json()["recurring_horizon_months"] == 1

        anchor = _next_weekday(date.today() + timedelta(days=7), 2)
        created = await c.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": types["roostervrij"]["id"],
                "anchor_date": anchor.isoformat(),
                "interval_weeks": 1,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        rows = await _generated(c, headers, member.id)
        assert len(rows) > 0
        assert all(
            date.fromisoformat(r["start_date"]) <= _add_months(date.today(), 1) for r in rows
        )


async def test_recurring_requires_profile_manage_and_is_tenant_isolated(client_for) -> None:
    a = await make_tenant("recurring-org-a")
    b = await make_tenant("recurring-org-b")
    ah = await auth_cookie(a.user)
    bh = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        member = await _member(ca, ah, "e@example.com")
        mh = await auth_cookie(member)
        types = await _types(ca, ah)
        anchor = _next_weekday(date.today() + timedelta(days=7), 4)

        # A plain member may not define patterns.
        res = await ca.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": types["roostervrij"]["id"],
                "anchor_date": anchor.isoformat(),
                "interval_weeks": 2,
            },
            headers=mh,
        )
        assert res.status_code == 403

        created = await ca.post(
            "/api/v1/leave/recurring",
            json={
                "user_id": str(member.id),
                "leave_type_id": types["roostervrij"]["id"],
                "anchor_date": anchor.isoformat(),
                "interval_weeks": 2,
            },
            headers=ah,
        )
        assert created.status_code == 201
        pattern_id = created.json()["id"]

    async with client_for(b.host) as cb:
        # B cannot see or touch A's pattern.
        res = await cb.get("/api/v1/leave/recurring", headers=bh)
        assert res.status_code == 200
        assert res.json() == []
        res = await cb.patch(
            f"/api/v1/leave/recurring/{pattern_id}", json={"active": False}, headers=bh
        )
        assert res.status_code == 404
