"""Freelance employment periods, and the availability a freelancer keeps themselves.

Two halves of one change:

* **A freelance period accrues nothing.** No statutory vacation, no free time. The interesting
  case is not "the pot is zero" but *which* rule produces the zero: a freelancer who also holds
  ``time.entry.write`` must not fall into the contract-less scheduled-hours fallback, which is
  the one path that would quietly hand them a year of hours nobody granted.
* **Availability is computed, not stored.** The base week bent by dated exceptions, with a "no"
  outranking a "yes" and a repeat expressed as a rule rather than as generated days.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant, org_today

_YEAR = org_today().year


async def _member(client, headers, email: str, role: str = "member") -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Member", "role": role},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _types(client, headers) -> dict[str, str]:
    rows = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return {t["key"]: t["id"] for t in rows}


async def _entitlements(client, headers, member_id) -> dict[str, float]:
    """This member's pots for the year, keyed by leave-type id."""
    rows = (
        await client.get(
            "/api/v1/leave/entitlements",
            params={"year": _YEAR, "user_id": str(member_id)},
            headers=headers,
        )
    ).json()
    return {e["leave_type_id"]: float(e["hours"]) for e in rows}


def _monday(offset_weeks: int = 0) -> date:
    """A Monday in November — the one month with no Dutch public holiday (see conftest)."""
    first = date(_YEAR, 11, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7, weeks=offset_weeks)


# --- the period ------------------------------------------------------------------ #


async def test_freelance_period_accrues_nothing_and_employee_still_does(client_for) -> None:
    t = await make_tenant("freelance-accrual")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        staff = await _member(c, headers, "payroll@example.com")
        zzp = await _member(c, headers, "zzp@example.com")

        for user_id, kind, hours in (
            (staff.id, "employee", "32"),
            (zzp.id, "freelance", "32"),
        ):
            res = await c.post(
                "/api/v1/leave/contracts",
                json={
                    "user_id": str(user_id),
                    "start_date": f"{_YEAR}-01-01",
                    "employment_type": kind,
                    "contract_hours_per_week": hours,
                },
                headers=headers,
            )
            assert res.status_code == 201, res.text

        # The payroll 32-h contract earns statutory hours *and* the norm shortfall as free time.
        payroll = await _entitlements(c, headers, staff.id)
        assert payroll.get(types["vacation_statutory"], 0) > 0
        assert payroll.get(types["roostervrij"], 0) > 0

        # The identical freelance period earns neither — nothing to prorate, nothing to place.
        freelance = await _entitlements(c, headers, zzp.id)
        assert freelance.get(types["vacation_statutory"], 0) == 0
        assert freelance.get(types["roostervrij"], 0) == 0
        # And the contract itself reports no accrual, so the wizard's preview cannot disagree.
        rows = (
            await c.get(
                "/api/v1/leave/contracts",
                params={"user_id": str(zzp.id)},
                headers=headers,
            )
        ).json()
        assert float(rows[0]["effective_free_time_per_week"]) == 0.0
        assert rows[0]["employment_type"] == "freelance"


async def test_freelancer_does_not_take_the_contractless_fallback(client_for) -> None:
    """The failure this change exists to prevent.

    ``seed_entitlements`` unions the contract holders with everyone holding
    ``time.entry.write`` so a contract-less org still generates — and a freelancer logs time. If
    a freelance period did not count as *having* a contract, that union would hand them a full
    year of statutory hours on the strength of a permission.
    """
    t = await make_tenant("freelance-fallback")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        zzp = await _member(c, headers, "logs-time@example.com")
        # No contract at all: reading the balance seeds the year (#108) and the legacy
        # scheduled-hours fallback hands them a pot.
        seeded = await c.get(
            "/api/v1/leave/balance",
            params={"year": _YEAR, "user_id": str(zzp.id)},
            headers=headers,
        )
        assert seeded.status_code == 200, seeded.text
        before = await _entitlements(c, headers, zzp.id)
        assert before.get(types["vacation_statutory"], 0) > 0

        res = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(zzp.id),
                "start_date": f"{_YEAR}-01-01",
                "employment_type": "freelance",
                "contract_hours_per_week": "24",
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        # The period re-derives the year (#264) and now earns nothing.
        after = await _entitlements(c, headers, zzp.id)
        assert after.get(types["vacation_statutory"], 0) == 0


async def test_switching_a_period_to_freelance_reprorates_the_year(client_for) -> None:
    t = await make_tenant("freelance-switch")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        member = await _member(c, headers, "switcher@example.com")
        created = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "contract_hours_per_week": "40",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["employment_type"] == "employee"  # the default, unasked
        assert await _entitlements(c, headers, member.id)

        moved = await c.patch(
            f"/api/v1/leave/contracts/{created.json()['id']}",
            json={"employment_type": "freelance"},
            headers=headers,
        )
        assert moved.status_code == 200, moved.text
        assert (await _entitlements(c, headers, member.id)) == {}

        back = await c.patch(
            f"/api/v1/leave/contracts/{created.json()['id']}",
            json={"employment_type": "employee"},
            headers=headers,
        )
        assert back.status_code == 200
        assert (await _entitlements(c, headers, member.id)).get(
            types["vacation_statutory"], 0
        ) > 0


async def test_hours_are_optional_for_freelance_and_required_for_payroll(client_for) -> None:
    t = await make_tenant("freelance-hours")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "nohours@example.com")

        # No fixed weekly commitment — an ordinary freelance arrangement.
        ok = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "employment_type": "freelance",
            },
            headers=headers,
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["contract_hours_per_week"] is None
        # It still resolves a week, so a roster and a capacity read have something to say.
        assert float(ok.json()["scheduled_hours_per_week"]) == 40.0

        other = await _member(c, headers, "payroll2@example.com")
        refused = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(other.id),
                "start_date": f"{_YEAR}-01-01",
                "employment_type": "employee",
            },
            headers=headers,
        )
        assert refused.status_code == 422
        assert "leave_contract_hours_required" in refused.text

        # And the same rule on the way back: a freelance period being made payroll must bring
        # its hours with it, or the statutory pot would prorate against a number nobody entered.
        promote = await c.patch(
            f"/api/v1/leave/contracts/{ok.json()['id']}",
            json={"employment_type": "employee"},
            headers=headers,
        )
        assert promote.status_code == 422
        assert "leave_contract_hours_required" in promote.text
        together = await c.patch(
            f"/api/v1/leave/contracts/{ok.json()['id']}",
            json={"employment_type": "employee", "contract_hours_per_week": "36"},
            headers=headers,
        )
        assert together.status_code == 200, together.text


# --- availability ----------------------------------------------------------------- #


async def test_availability_resolves_the_base_week_and_its_exceptions(client_for) -> None:
    t = await make_tenant("avail-days")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "avail@example.com")
        monday, saturday = _monday(), _monday() + timedelta(days=5)

        # An extra Saturday, and Monday afternoon off.
        for body in (
            {"user_id": str(member.id), "kind": "extra", "date": saturday.isoformat()},
            {
                "user_id": str(member.id),
                "kind": "unavailable",
                "date": monday.isoformat(),
                "start_time": "13:00",
            },
        ):
            res = await c.post("/api/v1/leave/availability", json=body, headers=headers)
            assert res.status_code == 201, res.text

        days = (
            await c.get(
                "/api/v1/leave/availability/days",
                params={
                    "date_from": monday.isoformat(),
                    "date_to": saturday.isoformat(),
                    "user_id": str(member.id),
                },
                headers=headers,
            )
        ).json()
        by_date = {d["date"]: d for d in days}

        # Monday: the morning only — 08:30–12:30 minus nothing, so four hours.
        assert float(by_date[monday.isoformat()]["hours"]) == 4.0
        assert by_date[monday.isoformat()]["deviates"] is True
        # Tuesday: untouched, a full scheduled day.
        tuesday = (monday + timedelta(days=1)).isoformat()
        assert float(by_date[tuesday]["hours"]) == 8.0
        assert by_date[tuesday]["deviates"] is False
        # Saturday: not in the week at all, so the whole-day extra takes the org default day.
        assert float(by_date[saturday.isoformat()]["hours"]) == 8.0
        assert by_date[saturday.isoformat()]["windows"] == [
            {"start": "08:30", "end": "12:30"},
            {"start": "13:00", "end": "17:00"},
        ]


async def test_a_no_outranks_a_yes_on_the_same_day(client_for) -> None:
    """Booking someone who said they were away is worse than missing a day they could work."""
    t = await make_tenant("avail-precedence")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "both@example.com")
        saturday = _monday() + timedelta(days=5)
        for kind in ("extra", "unavailable"):
            res = await c.post(
                "/api/v1/leave/availability",
                json={
                    "user_id": str(member.id),
                    "kind": kind,
                    "date": saturday.isoformat(),
                },
                headers=headers,
            )
            assert res.status_code == 201, res.text

        days = (
            await c.get(
                "/api/v1/leave/availability/days",
                params={
                    "date_from": saturday.isoformat(),
                    "date_to": saturday.isoformat(),
                    "user_id": str(member.id),
                },
                headers=headers,
            )
        ).json()
        assert days[0]["windows"] == []
        assert float(days[0]["hours"]) == 0.0


async def test_a_repeat_is_a_rule_not_a_row_per_occurrence(client_for) -> None:
    t = await make_tenant("avail-repeat")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "fortnight@example.com")
        anchor = _monday()
        created = await c.post(
            "/api/v1/leave/availability",
            json={
                "user_id": str(member.id),
                "kind": "unavailable",
                "date": anchor.isoformat(),
                "repeat_weeks": 2,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text

        days = (
            await c.get(
                "/api/v1/leave/availability/days",
                params={
                    "date_from": anchor.isoformat(),
                    "date_to": (anchor + timedelta(weeks=4)).isoformat(),
                    "user_id": str(member.id),
                },
                headers=headers,
            )
        ).json()
        blocked = {d["date"] for d in days if float(d["hours"]) == 0 and d["deviates"]}
        assert blocked == {
            anchor.isoformat(),
            (anchor + timedelta(weeks=2)).isoformat(),
            (anchor + timedelta(weeks=4)).isoformat(),
        }
        # The Monday in between is an ordinary working day — one row, three occurrences, and
        # nothing generated: the rule is still the rule next year.
        assert len(
            (
                await c.get(
                    "/api/v1/leave/availability",
                    params={
                        "date_from": anchor.isoformat(),
                        "date_to": (anchor + timedelta(weeks=4)).isoformat(),
                        "user_id": str(member.id),
                    },
                    headers=headers,
                )
            ).json()
        ) == 1

        # A bound with no cadence is refused rather than silently ignored.
        bad = await c.post(
            "/api/v1/leave/availability",
            json={
                "user_id": str(member.id),
                "kind": "extra",
                "date": anchor.isoformat(),
                "repeat_until": (anchor + timedelta(weeks=8)).isoformat(),
            },
            headers=headers,
        )
        assert bad.status_code == 422
        assert "leave_availability_repeat_required" in bad.text


async def test_a_move_is_a_pair_and_deleting_either_half_undoes_both(client_for) -> None:
    t = await make_tenant("avail-move")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "mover@example.com")
        tuesday = _monday() + timedelta(days=1)
        thursday = _monday() + timedelta(days=3)
        saturday = _monday() + timedelta(days=5)

        moved = await c.post(
            "/api/v1/leave/availability/move",
            json={
                "user_id": str(member.id),
                "from_date": tuesday.isoformat(),
                "to_date": saturday.isoformat(),
            },
            headers=headers,
        )
        assert moved.status_code == 201, moved.text
        rows = moved.json()
        assert {r["kind"] for r in rows} == {"unavailable", "extra"}
        assert rows[0]["pair_id"] == rows[1]["pair_id"] is not None

        days = {
            d["date"]: float(d["hours"])
            for d in (
                await c.get(
                    "/api/v1/leave/availability/days",
                    params={
                        "date_from": tuesday.isoformat(),
                        "date_to": saturday.isoformat(),
                        "user_id": str(member.id),
                    },
                    headers=headers,
                )
            ).json()
        }
        assert days[tuesday.isoformat()] == 0.0
        assert days[saturday.isoformat()] == 8.0
        assert days[thursday.isoformat()] == 8.0  # untouched

        # Undo from either end: half a move is a statement nobody made.
        gone = await c.delete(
            f"/api/v1/leave/availability/{rows[0]['id']}", headers=headers
        )
        assert gone.status_code == 204
        left = (
            await c.get(
                "/api/v1/leave/availability",
                params={
                    "date_from": tuesday.isoformat(),
                    "date_to": saturday.isoformat(),
                    "user_id": str(member.id),
                },
                headers=headers,
            )
        ).json()
        assert left == []

        same_day = await c.post(
            "/api/v1/leave/availability/move",
            json={
                "user_id": str(member.id),
                "from_date": tuesday.isoformat(),
                "to_date": tuesday.isoformat(),
            },
            headers=headers,
        )
        assert same_day.status_code == 422
        assert "leave_availability_move_same_day" in same_day.text


async def test_a_member_keeps_their_own_availability_but_not_a_colleague_s(client_for) -> None:
    """The default posture: a freelancer manages their own week without an admin (#310)."""
    t = await make_tenant("avail-scope")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        mine = await _member(c, headers, "mine@example.com")
        theirs = await _member(c, headers, "theirs@example.com")
        my_headers = await auth_cookie(mine)
        saturday = _monday() + timedelta(days=5)

        own = await c.post(
            "/api/v1/leave/availability",
            json={"kind": "extra", "date": saturday.isoformat()},
            headers=my_headers,
        )
        assert own.status_code == 201, own.text

        other = await c.post(
            "/api/v1/leave/availability",
            json={
                "user_id": str(theirs.id),
                "kind": "extra",
                "date": saturday.isoformat(),
            },
            headers=my_headers,
        )
        assert other.status_code == 403

        # Reading a colleague's *rows* needs `:any` too; the resolved day view is roster
        # information and stays team-visible.
        rows = await c.get(
            "/api/v1/leave/availability",
            params={
                "date_from": saturday.isoformat(),
                "date_to": saturday.isoformat(),
                "user_id": str(theirs.id),
            },
            headers=my_headers,
        )
        assert rows.status_code == 403


async def test_availability_is_tenant_isolated(client_for) -> None:
    a = await make_tenant("avail-iso-a")
    b = await make_tenant("avail-iso-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)
    saturday = _monday() + timedelta(days=5)
    async with client_for(a.host) as c:
        created = await c.post(
            "/api/v1/leave/availability",
            json={"kind": "extra", "date": saturday.isoformat()},
            headers=a_headers,
        )
        assert created.status_code == 201, created.text
        entry_id = created.json()["id"]

    async with client_for(b.host) as c:
        rows = await c.get(
            "/api/v1/leave/availability",
            params={
                "date_from": saturday.isoformat(),
                "date_to": saturday.isoformat(),
                "all_users": True,
            },
            headers=b_headers,
        )
        assert rows.status_code == 200
        assert rows.json() == []
        assert (
            await c.delete(f"/api/v1/leave/availability/{entry_id}", headers=b_headers)
        ).status_code == 404
