"""Server-side sorting for the leave lists (#40).

Managers approve leave on a phone, where there are no table headers to click, so the sort has to
exist on the server before the Kolommen menu can offer it. The interesting key is `employee`:
it orders by display name, never by user id, and falls back to the email exactly as the UI does.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from pwdlib import PasswordHash

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant

_ph = PasswordHash.recommended()


async def _member(org_id, email: str, full_name: str | None) -> User:
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=full_name,
            hashed_password=_ph.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, org_id)
        await add_membership(session, org_id, user.id, "member")
        await session.commit()
        return User(id=user.id, email=user.email, hashed_password="", is_active=True)


async def _leave_type(client, headers) -> str:
    res = await client.post(
        "/api/v1/leave/types",
        json={"key": "vacation", "label_i18n": {"nl": "Vakantie", "en": "Vacation"}},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _request(client, headers, type_id: str, start: date, hours: float = 8) -> str:
    res = await client.post(
        "/api/v1/leave/requests",
        json={
            "leave_type_id": type_id,
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
            "hours": hours,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_sorting_the_team_list_by_employee_uses_the_display_name(client_for) -> None:
    t = await make_tenant("lv-sort", email="owner@lv.test")
    zoe = await _member(t.org.id, "zoe@lv.test", "Zoe Zwart")
    ann = await _member(t.org.id, "ann@lv.test", "Ann Appel")
    # No full name: the UI shows the email, so the sort must file them under "b".
    bob = await _member(t.org.id, "bob@lv.test", None)

    async with client_for(t.host) as c:
        owner_headers = await auth_cookie(t.user)
        type_id = await _leave_type(c, owner_headers)

        today = date.today()
        for i, user in enumerate((zoe, ann, bob)):
            await _request(c, await auth_cookie(user), type_id, today + timedelta(days=i))

        page = (
            await c.get(
                "/api/v1/leave/requests?all_users=true&sort=employee", headers=owner_headers
            )
        ).json()
        names = [i["user_id"] for i in page["items"]]
        assert names == [str(ann.id), str(bob.id), str(zoe.id)]

        desc = (
            await c.get(
                "/api/v1/leave/requests?all_users=true&sort=-employee", headers=owner_headers
            )
        ).json()
        assert [i["user_id"] for i in desc["items"]] == [str(zoe.id), str(bob.id), str(ann.id)]


async def test_sorting_by_employee_never_duplicates_a_request(client_for) -> None:
    """The name subquery is correlated; a join would multiply the row."""
    t = await make_tenant("lv-dup")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        type_id = await _leave_type(c, headers)
        await _request(c, headers, type_id, date.today())
        page = (await c.get("/api/v1/leave/requests?sort=employee", headers=headers)).json()
        assert len(page["items"]) == 1
        assert page["total"] == 1


async def test_sorting_by_hours_and_start_date(client_for) -> None:
    t = await make_tenant("lv-fields")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        type_id = await _leave_type(c, headers)
        today = date.today()
        await _request(c, headers, type_id, today, hours=4)
        await _request(c, headers, type_id, today + timedelta(days=3), hours=16)

        by_hours = (await c.get("/api/v1/leave/requests?sort=hours", headers=headers)).json()
        assert [float(i["hours"]) for i in by_hours["items"]] == [4.0, 16.0]

        by_date = (await c.get("/api/v1/leave/requests?sort=-start_date", headers=headers)).json()
        assert by_date["items"][0]["start_date"] == (today + timedelta(days=3)).isoformat()


async def test_unknown_leave_sort_key_is_refused(client_for) -> None:
    t = await make_tenant("lv-bad")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        bad = await c.get("/api/v1/leave/requests?sort=hashed_password", headers=headers)
        assert bad.status_code == 400
        assert bad.json()["error"]["message"] == "errors.invalid_sort"


async def test_a_member_sorting_still_only_sees_their_own_requests(client_for) -> None:
    """Sorting must not become a way around the `all_users` manager gate."""
    t = await make_tenant("lv-scope")
    other = await _member(t.org.id, "other@lv-scope.test", "Other Person")
    async with client_for(t.host) as c:
        owner_headers = await auth_cookie(t.user)
        type_id = await _leave_type(c, owner_headers)
        await _request(c, owner_headers, type_id, date.today())
        await _request(c, await auth_cookie(other), type_id, date.today())

        other_headers = await auth_cookie(other)
        mine = (await c.get("/api/v1/leave/requests?sort=employee", headers=other_headers)).json()
        assert [i["user_id"] for i in mine["items"]] == [str(other.id)]


async def test_leave_sort_never_crosses_tenants(client_for) -> None:
    a = await make_tenant("lv-iso-a")
    b = await make_tenant("lv-iso-b")
    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        await _request(c, headers, await _leave_type(c, headers), date.today())
    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        page = (
            await c.get("/api/v1/leave/requests?all_users=true&sort=employee", headers=headers)
        ).json()
        assert page["items"] == []
