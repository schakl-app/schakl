"""Combined statutory + extra-statutory vacation balance, carry-over and expiry (#265).

The two Dutch vacation pots keep their own ``default_weeks`` and differing ``carry_over_months``
(so the legal wettelijk / bovenwettelijk split survives), but present to the employee as one
"Vakantieverlof" figure. A request spends the soonest-to-expire pot first, and — now that
``carry_over_months`` is actually enforced — unused carried hours lapse after their window.

Dates are anchored relative to ``_YEAR`` so expiry is deterministic whatever day the suite runs:
a statutory pot accrued two years ago expired on 1 July of *last* year (always in the past), an
extra-statutory pot accrued last year keeps five years (always still valid this year).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.auth.models import User
from tests.conftest import auth_cookie, leave_workday, make_tenant

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


async def _types(client, headers) -> dict[str, str]:
    rows = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return {t["key"]: t["id"] for t in rows}


def _span(week: int, length: int = 1) -> tuple[str, str]:
    """``length`` consecutive weekdays, ``week`` weeks into November (holiday-free, 8 h each)."""
    start = leave_workday(week * 5)
    return start.isoformat(), (start + timedelta(days=length - 1)).isoformat()


async def _put_ent(client, headers, user_id, type_id, year, hours) -> None:
    res = await client.put(
        "/api/v1/leave/entitlements",
        json={
            "user_id": str(user_id),
            "leave_type_id": type_id,
            "year": year,
            "hours": hours,
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text


async def _groups(client, headers, *, year: int = _YEAR, user_id=None) -> list[dict]:
    params: dict = {"year": year}
    if user_id is not None:
        params["user_id"] = str(user_id)
    res = await client.get("/api/v1/leave/balance/groups", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _per_type(client, headers, *, year: int = _YEAR, user_id=None) -> list[dict]:
    params: dict = {"year": year}
    if user_id is not None:
        params["user_id"] = str(user_id)
    res = await client.get("/api/v1/leave/balance", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _vacation(groups: list[dict]) -> dict:
    return next(g for g in groups if g["group"] == "vacation")


# --- the seed ------------------------------------------------------------------- #


async def test_vacation_types_share_a_balance_group(client_for) -> None:
    """The two seeded vacation types present as one group; the others stay standalone."""
    t = await make_tenant("vac-group-seed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        rows = (await c.get("/api/v1/leave/types", headers=headers)).json()
        by_key = {r["key"]: r for r in rows}
        assert by_key["vacation_statutory"]["balance_group"] == "vacation"
        assert by_key["vacation_extra"]["balance_group"] == "vacation"
        # Everything else is its own balance (ADV, sick, …): null = standalone.
        assert by_key["roostervrij"]["balance_group"] is None
        assert by_key["sick"]["balance_group"] is None


# --- combined entitlement (DoD: 38 h → 190, 36 h → 180) ------------------------- #


async def test_combined_vacation_entitlement_matches_five_weeks(client_for) -> None:
    """4 + 1 weeks of contract hours read as one figure: 38 h → 190, 36 h → 180."""
    for contract_hours, expected in (("38", 190.0), ("36", 180.0)):
        t = await make_tenant(f"vac-combine-{contract_hours}")
        headers = await auth_cookie(t.user)
        async with client_for(t.host) as c:
            member = await _member(c, headers, "e@example.com")
            # A full-year contract auto-seeds the entitlements it earns (#105).
            res = await c.post(
                "/api/v1/leave/contracts",
                json={
                    "user_id": str(member.id),
                    "start_date": f"{_YEAR}-01-01",
                    "end_date": f"{_YEAR}-12-31",
                    "contract_hours_per_week": contract_hours,
                },
                headers=headers,
            )
            assert res.status_code == 201, res.text

            group = _vacation(await _groups(c, headers, user_id=member.id))
            assert float(group["entitled_hours"]) == expected
            types = await _types(c, headers)
            assert set(group["leave_type_ids"]) == {
                types["vacation_statutory"],
                types["vacation_extra"],
            }
            # The label the employee sees is the combined one, not either underlying type's.
            assert group["label_i18n"]["nl"] == "Vakantieverlof"


# --- consumption order (DoD: soonest-to-expire pot first) ----------------------- #


async def test_request_draws_from_soonest_expiring_pot_first(client_for) -> None:
    """A vacation request spends statutory (expires in 6 months) before extra (5 years)."""
    t = await make_tenant("vac-fifo")
    headers = await auth_cookie(t.user)  # sole approver → may self-decide
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        await _put_ent(c, headers, t.user.id, types["vacation_statutory"], _YEAR, 152)
        await _put_ent(c, headers, t.user.id, types["vacation_extra"], _YEAR, 38)

        start, end = _span(0, 5)  # Mon–Fri = 40 h
        req = (
            await c.post(
                "/api/v1/leave/requests",
                json={
                    "leave_type_id": types["vacation_statutory"],
                    "start_date": start,
                    "end_date": end,
                },
                headers=headers,
            )
        ).json()
        assert (
            await c.post(
                f"/api/v1/leave/requests/{req['id']}/decide",
                json={"approved": True},
                headers=headers,
            )
        ).status_code == 200

        by_type = {b["leave_type_id"]: b for b in await _per_type(c, headers)}
        # Statutory took the whole 40 h; the long-lived extra pot is untouched.
        assert float(by_type[types["vacation_statutory"]]["remaining_hours"]) == 112.0
        assert float(by_type[types["vacation_extra"]]["remaining_hours"]) == 38.0

        group = _vacation(await _groups(c, headers))
        assert float(group["entitled_hours"]) == 190.0
        assert float(group["remaining_hours"]) == 150.0
        # Group remaining is exactly the sum of the per-type remaining — the two views agree.
        per_type_sum = sum(
            float(b["remaining_hours"])
            for b in by_type.values()
            if b["balance_group"] == "vacation"
        )
        assert float(group["remaining_hours"]) == per_type_sum


async def test_consumption_spills_to_the_next_pot_when_statutory_runs_out(client_for) -> None:
    """When the request exceeds statutory, the overflow — and only the overflow — hits extra."""
    t = await make_tenant("vac-spill")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        await _put_ent(c, headers, t.user.id, types["vacation_statutory"], _YEAR, 16)
        await _put_ent(c, headers, t.user.id, types["vacation_extra"], _YEAR, 40)

        start, end = _span(0, 3)  # 24 h > the 16 h statutory pot
        req = (
            await c.post(
                "/api/v1/leave/requests",
                json={
                    "leave_type_id": types["vacation_statutory"],
                    "start_date": start,
                    "end_date": end,
                },
                headers=headers,
            )
        ).json()
        await c.post(
            f"/api/v1/leave/requests/{req['id']}/decide",
            json={"approved": True},
            headers=headers,
        )

        by_type = {b["leave_type_id"]: b for b in await _per_type(c, headers)}
        assert float(by_type[types["vacation_statutory"]]["remaining_hours"]) == 0.0
        assert float(by_type[types["vacation_extra"]]["remaining_hours"]) == 32.0
        assert float(_vacation(await _groups(c, headers))["remaining_hours"]) == 32.0


# --- carry-over + expiry -------------------------------------------------------- #


async def test_extra_carries_over_and_old_statutory_has_expired(client_for) -> None:
    """Last year's extra-statutory carries into this year; a two-year-old statutory pot is gone."""
    t = await make_tenant("vac-carry")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        # A fresh current-year statutory pot (also suppresses the on-read auto-seed of #108).
        await _put_ent(c, headers, t.user.id, types["vacation_statutory"], _YEAR, 152)
        # Extra accrued last year keeps 5 years → still valid this year.
        await _put_ent(c, headers, t.user.id, types["vacation_extra"], _YEAR - 1, 40)
        # Statutory accrued two years ago expired on 1 July of last year → gone, whatever today is.
        await _put_ent(c, headers, t.user.id, types["vacation_statutory"], _YEAR - 2, 100)

        group = _vacation(await _groups(c, headers))
        # Available now = fresh statutory 152 + carried extra 40; the old statutory does not count.
        assert float(group["remaining_hours"]) == 192.0
        assert float(group["entitled_hours"]) == 192.0

        pots = {(p["leave_type_id"], p["accrual_year"]): p for p in group["pots"]}
        old = pots[(types["vacation_statutory"], _YEAR - 2)]
        assert old["expired"] is True
        assert float(old["remaining_hours"]) == 0.0
        carried = pots[(types["vacation_extra"], _YEAR - 1)]
        assert carried["expired"] is False
        assert float(carried["remaining_hours"]) == 40.0


async def test_standalone_adv_lapses_at_year_end(client_for) -> None:
    """ADV carries 0 months, so last year's unused rostered-free-day hours have lapsed (#265)."""
    t = await make_tenant("vac-adv-expiry")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        await _put_ent(c, headers, t.user.id, types["roostervrij"], _YEAR - 1, 16)

        groups = await _groups(c, headers)
        adv = next(g for g in groups if types["roostervrij"] in g["leave_type_ids"])
        # A standalone type is its own singleton group…
        assert adv["group"] is None
        # …and carry 0 means last year's pot lapsed on 1 January — nothing left to spend.
        assert float(adv["remaining_hours"]) == 0.0
        old = next(p for p in adv["pots"] if p["accrual_year"] == _YEAR - 1)
        assert old["expired"] is True


# --- over-request still submits and reads negative (#109), grouped -------------- #


async def test_grouped_over_request_reads_negative(client_for) -> None:
    """An over-request across the combined pool submits and the group balance reads negative."""
    t = await make_tenant("vac-over")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        # Total pool = 20 h; a 40 h week over-requests it by 20.
        await _put_ent(c, headers, t.user.id, types["vacation_statutory"], _YEAR, 12)
        await _put_ent(c, headers, t.user.id, types["vacation_extra"], _YEAR, 8)

        start, end = _span(0, 5)  # 40 h
        # The preview warns against the *combined* remaining, not the single stored type's.
        preview = (
            await c.post(
                "/api/v1/leave/requests/preview",
                json={
                    "leave_type_id": types["vacation_statutory"],
                    "start_date": start,
                    "end_date": end,
                },
                headers=headers,
            )
        ).json()
        assert float(preview["remaining_hours"]) == 20.0

        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": types["vacation_statutory"],
                "start_date": start,
                "end_date": end,
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text

        group = _vacation(await _groups(c, headers))
        assert float(group["remaining_hours"]) == -20.0
