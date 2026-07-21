"""The client-role horizon floor (issue #252).

A directly-invited ``client``-role member — no contact link, no group assignment — used to
fall through every horizon resolver and see the agency's entire roster. The floor
(``app/core/permissions/horizon.py``) closes that default: holding the client role restricts
the membership to the union of what the *other* sources grant, which for a bare invite is
nothing. Portal logins keep their contact's companies (the union widens the floor), and
staff roles are untouched — both covered by the existing portal/group suites.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker
from tests.conftest import auth_cookie, make_tenant


async def test_directly_invited_client_sees_no_companies(client_for) -> None:
    """The full roster, its count, and every company-scoped module read as empty — not as
    the whole tenant — for a client-role login nobody scoped to a company."""
    t = await make_tenant("client-floor")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Agency Client BV"}, headers=headers)
        ).json()
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "extern-floor@example.com", "role": "client"},
            headers=headers,
        )
        assert invited.status_code in (200, 201), invited.text

        async with async_session_maker() as session:
            client_user = await session.scalar(
                select(User).where(User.email == "extern-floor@example.com")
            )
            assert client_user is not None
        client_headers = await auth_cookie(client_user)

        # The list is empty and the total says so — no roster, no count leak.
        companies = await c.get("/api/v1/companies", headers=client_headers)
        assert companies.status_code == 200, companies.text
        assert companies.json()["items"] == []
        assert companies.json()["total"] == 0

        # Reading the company by id answers 404, never 403 — existence must not leak.
        assert (
            await c.get(f"/api/v1/companies/{company['id']}", headers=client_headers)
        ).status_code == 404

        # The other client-readable, company-scoped modules ride the same repo filter.
        domains = await c.get("/api/v1/domains", headers=client_headers)
        assert domains.status_code == 200, domains.text
        assert domains.json()["items"] == []
        websites = await c.get("/api/v1/websites", headers=client_headers)
        assert websites.status_code == 200, websites.text
        assert websites.json()["items"] == []

        # The owner still sees everything: the floor never touches staff.
        assert (await c.get("/api/v1/companies", headers=headers)).json()["total"] == 1
