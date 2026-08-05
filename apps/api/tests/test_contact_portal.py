"""Client portal (issue #193): invite round-trip, horizon, exclusions, deny-by-default."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.activity.models import ActivityLog
from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant


async def _tenant_with_contact(client_for, slug: str, *, companies: int = 1):
    t = await make_tenant(slug)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_ids = []
        for i in range(companies):
            company = (
                await c.post(
                    "/api/v1/companies", json={"name": f"Client {i}"}, headers=headers
                )
            ).json()
            company_ids.append(company["id"])
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": f"piet-{slug}@example.com",
                    "company_ids": company_ids[:1],
                },
                headers=headers,
            )
        ).json()
    return t, headers, contact, company_ids


async def test_portal_invite_round_trip(client_for) -> None:
    """Toggle on → user + client-role membership + linked contact; off → login refused, data
    intact; re-enable reuses the account."""
    t, headers, contact, _ = await _tenant_with_contact(client_for, "portal-rt")

    async with client_for(t.host) as c:
        # Nothing yet.
        state = await c.get(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        assert state.json()["status"] == "none"

        enabled = await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["status"] == "invited"
        # No transport configured in tests: reported, never silently swallowed.
        assert enabled.json()["invite_email_error"] == "errors.email_not_configured"

        # The portal login authenticates and holds the client role's read-only view.
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
            assert portal_user is not None
        portal_headers = await auth_cookie(portal_user)
        me = await c.get("/api/v1/meta/me", headers=portal_headers)
        assert me.status_code == 200
        assert me.json()["is_portal"] is True
        # marketing.metrics.read is a portal default grant (#193).
        assert "marketing.metrics.read" in me.json()["permissions"]

        # Disable → login refused (the cookie no longer authenticates), everything kept.
        disabled = await c.delete(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        assert disabled.json()["status"] == "disabled"
        assert (await c.get("/api/v1/meta/me", headers=portal_headers)).status_code == 401

        # Re-enable reuses the same account.
        again = await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        assert again.json()["status"] == "invited"
        assert (await c.get("/api/v1/meta/me", headers=portal_headers)).status_code == 200

        # The flips are on the contact's activity trail (§16).
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            actions = (
                (
                    await session.execute(
                        select(ActivityLog.action).where(
                            ActivityLog.entity_type == "contact",
                            ActivityLog.entity_id == uuid.UUID(contact["id"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert actions.count("portal_enabled") == 2
        assert "portal_disabled" in actions


async def test_portal_email_collision_is_refused(client_for) -> None:
    """An address already belonging to an account is a hard error — the client role is never
    silently attached to a staff login."""
    t, headers, contact, _ = await _tenant_with_contact(client_for, "portal-col")
    async with client_for(t.host) as c:
        # Point the contact's email at the owner's address.
        await c.patch(
            f"/api/v1/contacts/{contact['id']}",
            json={"email": t.user.email},
            headers=headers,
        )
        res = await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        assert res.status_code == 409
        assert res.json()["error"]["message"] == "errors.portal_email_in_use"

        # And no email at all cannot be invited.
        await c.patch(
            f"/api/v1/contacts/{contact['id']}", json={"email": ""}, headers=headers
        )
        res = await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        assert res.status_code == 422


async def test_portal_horizon_is_the_contacts_companies(client_for) -> None:
    """A portal login sees exactly its contact's companies — metrics included; 404 outside;
    linking/unlinking the contact moves the horizon on the next request."""
    t, headers, contact, company_ids = await _tenant_with_contact(
        client_for, "portal-horizon", companies=2
    )
    linked, other = company_ids

    async with client_for(t.host) as c:
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal_headers = await auth_cookie(portal_user)

        listed = (await c.get("/api/v1/companies", headers=portal_headers)).json()["items"]
        assert [row["id"] for row in listed] == [linked]
        assert (
            await c.get(f"/api/v1/companies/{other}", headers=portal_headers)
        ).status_code == 404

        # The curated marketing view is readable inside the horizon, 404 outside it.
        assert (
            await c.get(
                f"/api/v1/marketing/companies/{linked}/metrics", headers=portal_headers
            )
        ).status_code == 200
        assert (
            await c.get(
                f"/api/v1/marketing/companies/{other}/metrics", headers=portal_headers
            )
        ).status_code == 404

        # Staff surfaces refuse: deny-by-default RBAC is doing the rest.
        assert (await c.get("/api/v1/members", headers=portal_headers)).status_code == 403
        assert (
            await c.get("/api/v1/time/entries", headers=portal_headers)
        ).status_code in (403, 404)

        # Widen: link the contact to the second company — live on the next request.
        link = await c.post(
            f"/api/v1/contacts/{contact['id']}/links",
            json={"company_id": other},
            headers=headers,
        )
        assert link.status_code in (200, 201), link.text
        listed = (await c.get("/api/v1/companies", headers=portal_headers)).json()["items"]
        assert {row["id"] for row in listed} == {linked, other}


async def test_portal_user_excluded_from_notification_fanout(client_for) -> None:
    """A staff event must never land in a client's inbox (#193) — and "a client" is the role,
    not the contact link (#274): a directly-invited client-role member passed the contact-link
    test and received the staff fan-out."""
    from app.modules.notifications.service import NotificationService

    t, headers, contact, company_ids = await _tenant_with_contact(client_for, "portal-fan")
    async with client_for(t.host) as c:
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        await c.post(
            "/api/v1/members/invite",
            json={"email": "extern-fan@example.com", "role": "client"},
            headers=headers,
        )
    class _Ctx:
        pass

    async with async_session_maker() as session:
        portal_user = await session.scalar(select(User).where(User.email == contact["email"]))
        bare_client = await session.scalar(
            select(User).where(User.email == "extern-fan@example.com")
        )
        await set_current_org(session, t.org.id)
        emit_ctx = _Ctx()
        emit_ctx.org = t.org
        emit_ctx.session = session
        emit_ctx.user = None
        service = NotificationService(emit_ctx)
        kept = await service._members_only({t.user.id, portal_user.id, bare_client.id})
    assert t.user.id in kept
    assert portal_user.id not in kept
    assert bare_client.id not in kept


async def test_portal_user_excluded_from_staff_pickers(client_for) -> None:
    """#221: enabling the portal grants a `client` membership, but a client is not staff — the
    contact must not surface in the assignee/staff pickers fed by /members/lookup. Not even a
    permission-filtered lookup may leak them: the portal defaults grant marketing.metrics.read,
    so that filter alone would have matched."""
    t, headers, contact, _ = await _tenant_with_contact(client_for, "portal-picker")
    async with client_for(t.host) as c:
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)

        lookup = (await c.get("/api/v1/members/lookup", headers=headers)).json()
        emails = {m["email"] for m in lookup}
        assert contact["email"] not in emails
        assert t.user.email in emails

        filtered = await c.get(
            "/api/v1/members/lookup",
            params={"permission": "marketing.metrics.read"},
            headers=headers,
        )
        assert contact["email"] not in {m["email"] for m in filtered.json()}


async def test_portal_user_hidden_from_team_list(client_for) -> None:
    """A portal membership is managed from its contact's portal section, not Instellingen →
    Gebruikers: the members list is staff-only. A directly-invited client-role member (no
    contact link) stays listed — hiding them would orphan them."""
    t, headers, contact, _ = await _tenant_with_contact(client_for, "portal-team")
    async with client_for(t.host) as c:
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        await c.post(
            "/api/v1/members/invite",
            json={"email": "extern@example.com", "role": "client"},
            headers=headers,
        )
        emails = {m["email"] for m in (await c.get("/api/v1/members", headers=headers)).json()}
        assert contact["email"] not in emails
        assert "extern@example.com" in emails
        assert t.user.email in emails


async def test_portal_reads_only_their_companies_contacts(client_for) -> None:
    """Contacts carry no ``company_id`` column, so the generic horizon filter (#191) never
    touched them — a portal login could read the org's whole address book. Now a client sees
    only people linked to a company inside the horizon; other companies' people and unlinked
    contacts are absent, and out-of-horizon reads answer 404, never 403."""
    t, headers, contact, _ = await _tenant_with_contact(client_for, "portal-abook")
    async with client_for(t.host) as c:
        other_company = (
            await c.post("/api/v1/companies", json={"name": "Other BV"}, headers=headers)
        ).json()
        other_contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Truus",
                    "last_name": "Anders",
                    "company_ids": [other_company["id"]],
                },
                headers=headers,
            )
        ).json()
        unlinked = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "Zwevend", "last_name": "Niemand"},
                headers=headers,
            )
        ).json()

        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal_headers = await auth_cookie(portal_user)

        listed = (await c.get("/api/v1/contacts", headers=portal_headers)).json()
        ids = {row["id"] for row in listed["items"]}
        assert contact["id"] in ids
        assert other_contact["id"] not in ids
        assert unlinked["id"] not in ids
        assert listed["total"] == 1

        # Get-by-id outside the horizon: 404, the same answer a nonexistent id gets.
        r = await c.get(f"/api/v1/contacts/{other_contact['id']}", headers=portal_headers)
        assert r.status_code == 404
        # Filtering on an out-of-horizon company answers 404, like reading the company does.
        r = await c.get(
            "/api/v1/contacts",
            params={"company_id": other_company["id"]},
            headers=portal_headers,
        )
        assert r.status_code == 404
        # Staff reads are untouched: the owner still sees the whole address book.
        assert (await c.get("/api/v1/contacts", headers=headers)).json()["total"] == 3


async def test_portal_contact_read_hides_out_of_horizon_links(client_for) -> None:
    """A person visible to a client may also work for one of the agency's other clients. The
    read may not say so: ``ContactRead.companies`` was org-scoped only, so the roster #252 took
    away came back one colleague at a time — with names and ids."""
    t, headers, contact, company_ids = await _tenant_with_contact(
        client_for, "portal-crosslink", companies=2
    )
    mine, theirs = company_ids

    async with client_for(t.host) as c:
        # A colleague at the client's own company who also works for the agency's other client.
        colleague = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Dubbel",
                    "last_name": "Verbonden",
                    "company_ids": [mine, theirs],
                },
                headers=headers,
            )
        ).json()
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal_headers = await auth_cookie(portal_user)

        # The colleague is readable (linked to a company in the horizon) — but only that link
        # is named. The other client's id and name are not the client's business.
        seen = (
            await c.get(f"/api/v1/contacts/{colleague['id']}", headers=portal_headers)
        ).json()
        assert [link["company_id"] for link in seen["companies"]] == [mine]
        listed = (await c.get("/api/v1/contacts", headers=portal_headers)).json()["items"]
        by_id = {row["id"]: row for row in listed}
        assert [link["company_id"] for link in by_id[colleague["id"]]["companies"]] == [mine]

        # Staff still see both links on the same record.
        staff = (await c.get(f"/api/v1/contacts/{colleague['id']}", headers=headers)).json()
        assert {link["company_id"] for link in staff["companies"]} == {mine, theirs}


async def test_portal_can_add_a_contact_to_its_own_company(client_for) -> None:
    """#274, the reported defect: granting a client role ``contacts.contact.write`` +
    ``contacts.link.write`` still answered 404.

    ``create`` wrote the contact and then called the *public* ``link``, which re-reads the
    contact through the portal repo — and that repo demands an existing company link, which a
    row created one statement ago cannot have. So the only contact a client could add was one
    that already existed. Attaching to a company *outside* the horizon must still 404.
    """
    t, headers, contact, company_ids = await _tenant_with_contact(
        client_for, "portal-addcontact", companies=2
    )
    mine, theirs = company_ids

    async with client_for(t.host) as c:
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        roles = (await c.get("/api/v1/roles", headers=headers)).json()
        client_role = next(r for r in roles if r["key"] == "client")
        granted = await c.patch(
            f"/api/v1/roles/{client_role['id']}",
            json={
                "permissions": sorted(
                    set(client_role["permissions"])
                    | {"contacts.contact.write", "contacts.link.write"}
                )
            },
            headers=headers,
        )
        assert granted.status_code == 200, granted.text

        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal_headers = await auth_cookie(portal_user)

        created = await c.post(
            "/api/v1/contacts",
            json={
                "first_name": "Nieuwe",
                "last_name": "Collega",
                "email": "collega-274@example.com",
                "company_ids": [mine],
            },
            headers=portal_headers,
        )
        assert created.status_code == 201, created.text
        assert [link["company_id"] for link in created.json()["companies"]] == [mine]
        # And they can read back what they just added — the link is what makes it theirs.
        assert (
            await c.get(f"/api/v1/contacts/{created.json()['id']}", headers=portal_headers)
        ).status_code == 200

        # A company outside the horizon is still 404, and nothing is written on the way there.
        refused = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Bij", "last_name": "Anderen", "company_ids": [theirs]},
            headers=portal_headers,
        )
        assert refused.status_code == 404, refused.text
        assert (await c.get("/api/v1/contacts", headers=headers)).json()["total"] == 2

        # A contact attached to nothing would vanish on the next read, so it is refused too.
        floating = await c.post(
            "/api/v1/contacts", json={"first_name": "Zwevend"}, headers=portal_headers
        )
        assert floating.status_code == 422, floating.text
        assert (
            floating.json()["error"]["fields"]["company_ids"]
            == "errors.contact_company_required"
        )


async def test_portal_link_mutations_respect_the_horizon(client_for) -> None:
    """``set_primary`` and ``unlink`` take a company id straight from the caller and looked the
    link up org-wide, so a scoped login could re-primary or detach a contact at a company it
    cannot see (#191's write rule, missed on these two paths)."""
    t, headers, contact, company_ids = await _tenant_with_contact(
        client_for, "portal-linkguard", companies=2
    )
    mine, theirs = company_ids

    async with client_for(t.host) as c:
        other_contact = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "Truus", "last_name": "Anders", "company_ids": [theirs]},
                headers=headers,
            )
        ).json()
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        roles = (await c.get("/api/v1/roles", headers=headers)).json()
        client_role = next(r for r in roles if r["key"] == "client")
        await c.patch(
            f"/api/v1/roles/{client_role['id']}",
            json={"permissions": sorted(set(client_role["permissions"]) | {"contacts.link.write"})},
            headers=headers,
        )
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal_headers = await auth_cookie(portal_user)

        assert (
            await c.delete(
                f"/api/v1/contacts/{other_contact['id']}/links/{theirs}", headers=portal_headers
            )
        ).status_code == 404
        assert (
            await c.patch(
                f"/api/v1/contacts/{other_contact['id']}/links/{theirs}",
                json={"is_primary": True},
                headers=portal_headers,
            )
        ).status_code == 404
        # The link is still there for staff.
        staff = (
            await c.get(f"/api/v1/contacts/{other_contact['id']}", headers=headers)
        ).json()
        assert [link["company_id"] for link in staff["companies"]] == [theirs]


async def test_portal_state_is_tenant_scoped(client_for) -> None:
    t, headers, contact, _ = await _tenant_with_contact(client_for, "portal-iso")
    other = await make_tenant("portal-iso-other")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as c:
        assert (
            await c.get(f"/api/v1/portal/logins/contact/{contact['id']}", headers=other_headers)
        ).status_code == 404


async def test_portal_sees_only_client_visible_tasks(client_for) -> None:
    """A portal login sees a task only when staff ticked visible_to_client — on the list, by
    id, and as a comment target; commenting on a visible task works (client own-grant)."""
    t, headers, contact, company_ids = await _tenant_with_contact(client_for, "portal-tasks")
    company = company_ids[0]

    async with client_for(t.host) as c:
        visible = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Zichtbaar", "company_id": company, "visible_to_client": True},
                headers=headers,
            )
        ).json()
        hidden = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Intern", "company_id": company},
                headers=headers,
            )
        ).json()

        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal_headers = await auth_cookie(portal_user)

        titles = [
            r["title"]
            for r in (await c.get("/api/v1/tasks?limit=50", headers=portal_headers)).json()[
                "items"
            ]
        ]
        assert titles == ["Zichtbaar"]
        assert (
            await c.get(f"/api/v1/tasks/{visible['id']}", headers=portal_headers)
        ).status_code == 200
        # The unticked task is absent, not forbidden — and its comment path with it.
        assert (
            await c.get(f"/api/v1/tasks/{hidden['id']}", headers=portal_headers)
        ).status_code == 404
        assert (
            await c.post(
                f"/api/v1/tasks/{hidden['id']}/comments",
                json={"body": "hoi"},
                headers=portal_headers,
            )
        ).status_code == 404

        # Commenting on the visible task is exactly what the checkbox is for.
        commented = await c.post(
            f"/api/v1/tasks/{visible['id']}/comments",
            json={"body": "Vraagje over de planning"},
            headers=portal_headers,
        )
        assert commented.status_code == 201, commented.text

        # The staff activity feed stays out of portal reach entirely.
        feed = await c.get(
            f"/api/v1/activity?entity_type=task&entity_id={visible['id']}",
            headers=portal_headers,
        )
        assert feed.json() == []

        # Staff keep seeing both, whatever the flag.
        staff_titles = {
            r["title"]
            for r in (await c.get("/api/v1/tasks?limit=50", headers=headers)).json()["items"]
        }
        assert {"Zichtbaar", "Intern"} <= staff_titles
