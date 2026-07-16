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
        state = (await c.get(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)).json()
        assert state["status"] == "none"

        enabled = await c.post(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
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
        disabled = await c.delete(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
        assert disabled.json()["status"] == "disabled"
        assert (await c.get("/api/v1/meta/me", headers=portal_headers)).status_code == 401

        # Re-enable reuses the same account.
        again = await c.post(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
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
        res = await c.post(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
        assert res.status_code == 409
        assert res.json()["error"]["message"] == "errors.portal_email_in_use"

        # And no email at all cannot be invited.
        await c.patch(
            f"/api/v1/contacts/{contact['id']}", json={"email": ""}, headers=headers
        )
        res = await c.post(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
        assert res.status_code == 422


async def test_portal_horizon_is_the_contacts_companies(client_for) -> None:
    """A portal login sees exactly its contact's companies — metrics included; 404 outside;
    linking/unlinking the contact moves the horizon on the next request."""
    t, headers, contact, company_ids = await _tenant_with_contact(
        client_for, "portal-horizon", companies=2
    )
    linked, other = company_ids

    async with client_for(t.host) as c:
        await c.post(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
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
    """A staff event must never land in a client's inbox (#193)."""
    from app.modules.notifications.service import NotificationService

    t, headers, contact, company_ids = await _tenant_with_contact(client_for, "portal-fan")
    async with client_for(t.host) as c:
        await c.post(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
    class _Ctx:
        pass

    async with async_session_maker() as session:
        portal_user = await session.scalar(select(User).where(User.email == contact["email"]))
        await set_current_org(session, t.org.id)
        emit_ctx = _Ctx()
        emit_ctx.org = t.org
        emit_ctx.session = session
        emit_ctx.user = None
        service = NotificationService(emit_ctx)
        kept = await service._members_only({t.user.id, portal_user.id})
    assert t.user.id in kept
    assert portal_user.id not in kept


async def test_portal_state_is_tenant_scoped(client_for) -> None:
    t, headers, contact, _ = await _tenant_with_contact(client_for, "portal-iso")
    other = await make_tenant("portal-iso-other")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as c:
        assert (
            await c.get(f"/api/v1/contacts/{contact['id']}/portal", headers=other_headers)
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

        await c.post(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
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
