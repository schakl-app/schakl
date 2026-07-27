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


async def _grant_client_role(c, headers, extra: list[str]) -> None:
    """Add permissions to the seeded ``client`` role, the way an admin does in Rollen."""
    roles = (await c.get("/api/v1/roles", headers=headers)).json()
    client_role = next(r for r in roles if r["key"] == "client")
    res = await c.patch(
        f"/api/v1/roles/{client_role['id']}",
        json={"permissions": sorted(set(client_role["permissions"]) | set(extra))},
        headers=headers,
    )
    assert res.status_code == 200, res.text


async def test_directly_invited_client_cannot_read_the_address_book(client_for) -> None:
    """Contacts carry no ``company_id``, so the floor's repo filter never reached them: the
    narrowing was gated on "is contact-linked" (#193) rather than "is a client login", and a
    directly-invited client-role member fell past it into the org's whole address book (#274).

    They also cannot *write* one: with nothing in their horizon the refusal names the missing
    link instead of the bare ``errors.not_found`` the customer reported.
    """
    t = await make_tenant("client-abook")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        await c.post(
            "/api/v1/contacts",
            json={
                "first_name": "Geheim",
                "last_name": "Persoon",
                "email": "geheim-floor@example.com",
                "company_ids": [company["id"]],
            },
            headers=headers,
        )
        await c.post(
            "/api/v1/members/invite",
            json={"email": "extern-abook@example.com", "role": "client"},
            headers=headers,
        )
        await _grant_client_role(c, headers, ["contacts.contact.write", "contacts.link.write"])

        async with async_session_maker() as session:
            client_user = await session.scalar(
                select(User).where(User.email == "extern-abook@example.com")
            )
        client_headers = await auth_cookie(client_user)

        listed = await c.get("/api/v1/contacts", headers=client_headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"] == []
        assert listed.json()["total"] == 0

        # Writing is refused with the reason, not with "not found" (#274's support dead end).
        created = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Nieuwe", "company_ids": [company["id"]]},
            headers=client_headers,
        )
        assert created.status_code == 403, created.text
        assert created.json()["error"]["message"] == "errors.no_company_scope"
        # …and a floating contact is refused too: it would be invisible to them the moment it
        # is saved, so it is never written.
        assert (
            await c.post(
                "/api/v1/contacts", json={"first_name": "Zwevend"}, headers=client_headers
            )
        ).status_code == 403

        # Staff are untouched: the owner still reads the address book.
        assert (await c.get("/api/v1/contacts", headers=headers)).json()["total"] == 1


async def test_members_list_flags_a_client_scoped_to_nothing(client_for) -> None:
    """The admin-facing signal (#274): Instellingen → Gebruikers says *why* the customer's
    login sees nothing, so "grant the permission" stops being the only thing left to try."""
    t = await make_tenant("client-flag")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/members/invite",
            json={"email": "extern-flag@example.com", "role": "client"},
            headers=headers,
        )
        members = {m["email"]: m for m in (await c.get("/api/v1/members", headers=headers)).json()}
        assert members["extern-flag@example.com"]["company_scope_empty"] is True
        # Staff are never flagged — the floor is the client role's alone.
        assert members[t.user.email]["company_scope_empty"] is False
