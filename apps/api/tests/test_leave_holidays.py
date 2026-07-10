"""The holiday calendar: the generator, the import's replace rules, and the API (#47).

The generator gets its own tests because a pasted date table is right for two years and wrong
forever after — these assert the *rule*, on years nobody has hand-checked.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from app.modules.leave.holidays import dutch_holidays, easter_sunday, kingsday
from app.modules.leave.jobs import import_next_year_holidays
from app.modules.leave.models import LeaveHoliday
from tests.conftest import auth_cookie, make_tenant


def _by_key(year: int) -> dict[str, date]:
    return {h.key: h.day for h in dutch_holidays(year)}


# --- the generator ------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("year", "expected"),
    [(2026, date(2026, 4, 5)), (2027, date(2027, 3, 28)), (2028, date(2028, 4, 16))],
)
def test_easter_sunday(year: int, expected: date) -> None:
    assert easter_sunday(year) == expected


def test_dutch_holidays_2026() -> None:
    """The list in issue #47: do 1 jan · vr 3 apr · zo 5 + ma 6 apr · ma 27 apr · di 5 mei ·
    do 14 mei · zo 24 + ma 25 mei · vr 25 + za 26 dec."""
    days = _by_key(2026)
    assert days == {
        "nieuwjaarsdag": date(2026, 1, 1),
        "goede_vrijdag": date(2026, 4, 3),
        "eerste_paasdag": date(2026, 4, 5),
        "tweede_paasdag": date(2026, 4, 6),
        "koningsdag": date(2026, 4, 27),
        "bevrijdingsdag": date(2026, 5, 5),
        "hemelvaartsdag": date(2026, 5, 14),
        "eerste_pinksterdag": date(2026, 5, 24),
        "tweede_pinksterdag": date(2026, 5, 25),
        "eerste_kerstdag": date(2026, 12, 25),
        "tweede_kerstdag": date(2026, 12, 26),
    }


def test_dutch_holidays_2027() -> None:
    """vr 1 jan · vr 26 mrt · zo 28 + ma 29 mrt · di 27 apr · wo 5 mei · do 6 mei ·
    zo 16 + ma 17 mei · za 25 + zo 26 dec."""
    days = _by_key(2027)
    assert days["goede_vrijdag"] == date(2027, 3, 26)
    assert days["tweede_paasdag"] == date(2027, 3, 29)
    assert days["koningsdag"] == date(2027, 4, 27)
    assert days["hemelvaartsdag"] == date(2027, 5, 6)
    assert days["tweede_pinksterdag"] == date(2027, 5, 17)
    assert days["tweede_kerstdag"] == date(2027, 12, 26)


def test_dutch_holidays_2028_needs_no_code_change() -> None:
    days = _by_key(2028)
    assert days["eerste_paasdag"] == date(2028, 4, 16)
    assert days["hemelvaartsdag"] == date(2028, 5, 25)
    assert days["tweede_pinksterdag"] == date(2028, 6, 5)


def test_koningsdag_moves_back_when_the_27th_is_a_sunday() -> None:
    """Not 2030 (issue #47 says so, and is wrong): 27 April 2030 is a Saturday, and Koningsdag
    only moves for a Sunday. 2025 and 2031 are the Sundays either side of today."""
    assert date(2030, 4, 27).weekday() == 5  # Saturday — no shift
    assert kingsday(2030) == date(2030, 4, 27)

    assert date(2031, 4, 27).weekday() == 6  # Sunday — shift back to the 26th
    assert kingsday(2031) == date(2031, 4, 26)
    assert kingsday(2025) == date(2025, 4, 26)
    assert kingsday(2026) == date(2026, 4, 27)


# --- the API ------------------------------------------------------------------- #
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


async def test_holidays_are_seeded_for_a_new_org(client_for) -> None:
    tenant = await make_tenant("leave-hol-seed")
    headers = await auth_cookie(tenant.user)
    year = date.today().year
    async with client_for(tenant.host) as client:
        res = await client.get(f"/api/v1/leave/holidays?year={year}", headers=headers)
        assert res.status_code == 200
        rows = res.json()
        assert {r["key"] for r in rows} == set(_by_key(year))
        assert all(r["source"] == "nl" and r["active"] for r in rows)
        # Next year too, so December's requests already cost the right hours.
        ahead = await client.get(f"/api/v1/leave/holidays?year={year + 1}", headers=headers)
        assert len(ahead.json()) == 11


async def test_deactivating_a_holiday_hides_it_and_survives_reimport(client_for) -> None:
    """Goede Vrijdag is worked at many Dutch employers. Switching it off must stick (#47)."""
    tenant = await make_tenant("leave-hol-deactivate")
    headers = await auth_cookie(tenant.user)
    year = date.today().year
    async with client_for(tenant.host) as client:
        rows = (await client.get(f"/api/v1/leave/holidays?year={year}", headers=headers)).json()
        good_friday = next(r for r in rows if r["key"] == "goede_vrijdag")

        off = await client.patch(
            f"/api/v1/leave/holidays/{good_friday['id']}", json={"active": False}, headers=headers
        )
        assert off.status_code == 200

        visible = (await client.get(f"/api/v1/leave/holidays?year={year}", headers=headers)).json()
        assert "goede_vrijdag" not in {r["key"] for r in visible}

        # A re-import must not resurrect it.
        result = await client.post(
            "/api/v1/leave/holidays/import", json={"year": year}, headers=headers
        )
        assert result.status_code == 200
        assert result.json() == {"created": 0, "updated": 0, "skipped": 11}

        after = (await client.get(f"/api/v1/leave/holidays?year={year}", headers=headers)).json()
        assert "goede_vrijdag" not in {r["key"] for r in after}
        # …but it still exists, deactivated, for Settings to show.
        all_rows = (
            await client.get(
                f"/api/v1/leave/holidays?year={year}&include_inactive=true", headers=headers
            )
        ).json()
        assert next(r for r in all_rows if r["key"] == "goede_vrijdag")["active"] is False


async def test_manual_holidays_survive_a_reimport(client_for) -> None:
    tenant = await make_tenant("leave-hol-manual")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        # A tenant-specific day off nobody's generator knows about.
        created = await client.post(
            "/api/v1/leave/holidays",
            json={"date": "2029-08-14", "name_i18n": {"nl": "Bureaudag", "en": "Agency day"}},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["source"] == "manual"
        assert created.json()["key"] is None

        imported = await client.post(
            "/api/v1/leave/holidays/import", json={"year": 2029}, headers=headers
        )
        assert imported.json()["created"] == 11

        rows = (await client.get("/api/v1/leave/holidays?year=2029", headers=headers)).json()
        assert len(rows) == 12
        assert next(r for r in rows if r["key"] is None)["name_i18n"]["nl"] == "Bureaudag"

        # Two rows may not occupy one day.
        clash = await client.post(
            "/api/v1/leave/holidays", json={"date": "2029-08-14", "name_i18n": {}}, headers=headers
        )
        assert clash.status_code == 409
        assert "errors.leave_holiday_exists" in clash.text


async def test_reimport_moves_a_generated_row_rather_than_duplicating_it(client_for) -> None:
    """Koningsdag on a Sunday moves to the 26th. It must move, not appear twice (#47)."""
    tenant = await make_tenant("leave-hol-move")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await client.post("/api/v1/leave/holidays/import", json={"year": 2031}, headers=headers)
        rows = (await client.get("/api/v1/leave/holidays?year=2031", headers=headers)).json()
        koningsdag = next(r for r in rows if r["key"] == "koningsdag")
        assert koningsdag["date"] == "2031-04-26"

        # Simulate a row generated by an older, buggy rule that put it on the 27th.
        await client.patch(
            f"/api/v1/leave/holidays/{koningsdag['id']}",
            json={"date": "2031-04-27"},
            headers=headers,
        )
        result = await client.post(
            "/api/v1/leave/holidays/import", json={"year": 2031}, headers=headers
        )
        assert result.json()["updated"] == 1
        assert result.json()["created"] == 0

        after = (await client.get("/api/v1/leave/holidays?year=2031", headers=headers)).json()
        kings = [r for r in after if r["key"] == "koningsdag"]
        assert len(kings) == 1  # moved, not duplicated
        assert kings[0]["date"] == "2031-04-26"


async def test_import_is_idempotent(client_for) -> None:
    tenant = await make_tenant("leave-hol-idempotent")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        first = await client.post(
            "/api/v1/leave/holidays/import", json={"year": 2033}, headers=headers
        )
        assert first.json() == {"created": 11, "updated": 0, "skipped": 0}
        second = await client.post(
            "/api/v1/leave/holidays/import", json={"year": 2033}, headers=headers
        )
        assert second.json() == {"created": 0, "updated": 0, "skipped": 11}


async def test_settings_update_is_partial(client_for) -> None:
    """Two screens save here. A full replace would let the schedule screen reset the holiday
    config, and the holiday screen reset the schedule, whichever shipped first."""
    tenant = await make_tenant("leave-hol-partial")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        # The holiday screen turns auto-import off, and says nothing about the schedule.
        await client.put(
            "/api/v1/leave/settings", json={"holiday_auto_import": False}, headers=headers
        )
        # The schedule screen saves a schedule, and says nothing about holidays.
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
        body = res.json()
        assert body["default_schedule"]["mon"]["start"] == "09:00"
        assert body["holiday_auto_import"] is False  # not reset to the default


async def test_members_read_holidays_but_cannot_manage_them(client_for) -> None:
    tenant = await make_tenant("leave-hol-rbac")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        member = await _invite_member(client, headers, "reader@example.com")
        member_headers = await auth_cookie(member)
        year = date.today().year

        # The calendar is normal team-visible information.
        listed = await client.get(f"/api/v1/leave/holidays?year={year}", headers=member_headers)
        assert listed.status_code == 200
        assert len(listed.json()) == 11

        blocked = await client.post(
            "/api/v1/leave/holidays",
            json={"date": f"{year}-08-14", "name_i18n": {}},
            headers=member_headers,
        )
        assert blocked.status_code == 403
        assert (
            await client.post(
                "/api/v1/leave/holidays/import", json={"year": year}, headers=member_headers
            )
        ).status_code == 403


async def test_holiday_tenant_isolation(client_for) -> None:
    """Org A cannot see, edit or delete org B's holidays (Golden Rule 1)."""
    a = await make_tenant("leave-hol-a")
    b = await make_tenant("leave-hol-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)

    async with client_for(a.host) as client:
        made = await client.post(
            "/api/v1/leave/holidays",
            json={"date": "2029-03-03", "name_i18n": {"nl": "Bureaudag A"}},
            headers=a_headers,
        )
        assert made.status_code == 201
        a_id = made.json()["id"]

    async with client_for(b.host) as client:
        # B's own 2029 calendar exists and holds nothing of A's.
        rows = (await client.get("/api/v1/leave/holidays?year=2029", headers=b_headers)).json()
        assert "2029-03-03" not in {r["date"] for r in rows}
        assert (
            await client.patch(
                f"/api/v1/leave/holidays/{a_id}", json={"active": False}, headers=b_headers
            )
        ).status_code == 404
        assert (
            await client.delete(f"/api/v1/leave/holidays/{a_id}", headers=b_headers)
        ).status_code == 404

    async with client_for(a.host) as client:
        still = (await client.get("/api/v1/leave/holidays?year=2029", headers=a_headers)).json()
        assert next(r for r in still if r["date"] == "2029-03-03")["active"] is True


# --- the cron job -------------------------------------------------------------- #
async def test_cron_imports_next_year_per_org(client_for) -> None:
    """``run_per_org`` binds RLS per tenant; every active org gets next year, once."""
    a = await make_tenant("leave-cron-a")
    b = await make_tenant("leave-cron-b")
    next_year = date.today().year + 1

    await import_next_year_holidays({})

    for tenant in (a, b):
        async with async_session_maker() as session:
            await set_current_org(session, tenant.org.id)
            rows = (
                (
                    await session.execute(
                        select(LeaveHoliday).where(
                            LeaveHoliday.date >= date(next_year, 1, 1),
                            LeaveHoliday.date < date(next_year + 1, 1, 1),
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {r.key for r in rows} == set(_by_key(next_year))
            assert all(r.org_id == tenant.org.id for r in rows)

    # Running it again creates nothing (idempotent, and it is a yearly cron).
    await import_next_year_holidays({})
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        count = len((await session.execute(select(LeaveHoliday))).scalars().all())
        assert count == 11


async def test_cron_respects_holiday_auto_import_off(client_for) -> None:
    tenant = await make_tenant("leave-cron-off")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        settings = (await client.get("/api/v1/leave/settings", headers=headers)).json()
        settings["holiday_auto_import"] = False
        assert (
            await client.put("/api/v1/leave/settings", json=settings, headers=headers)
        ).status_code == 200

    await import_next_year_holidays({})

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        assert (await session.execute(select(LeaveHoliday))).scalars().all() == []
