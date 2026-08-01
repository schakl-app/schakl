"""Signing in as a client's contact person (#296).

The agency-side counterpart of the instance owner's impersonation (issue #26): staff who hold
``contacts.portal.impersonate`` may become one of their clients' portal logins, on their own
tenant, for a time-boxed window. What these tests pin is not the happy path so much as the four
properties that make it safe to hand a tenant at all:

* it needs the permission, and nothing else implies it;
* it can never *gain* the caller a capability (``PermissionSet.covers``);
* it cannot cross a tenant, and a grant is bound to the person it was issued to;
* it is never silent — start, stop, and **every write made while it runs** name the impersonator.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.core.activity.models import ActivityLog
from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant

IMPERSONATE = "contacts.portal.impersonate"


async def _portal_contact(client_for, slug: str):
    """A tenant with one client, one contact at it, and a portal login for that contact."""
    tenant = await make_tenant(slug)
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Client BV"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": f"piet-{slug}@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        enabled = await c.post(
            f"/api/v1/contacts/{contact['id']}/portal", headers=headers
        )
        assert enabled.status_code == 200, enabled.text
    async with async_session_maker() as session:
        portal_user = await session.scalar(
            select(User).where(User.email == contact["email"])
        )
    return tenant, headers, contact, company, portal_user


def _both_cookies(session_headers: dict[str, str], token: str) -> dict[str, str]:
    """The staff session **and** the grant. Neither works alone — that is the design (#26)."""
    return {"Cookie": f"{session_headers['Cookie']}; schakl_impersonate={token}"}


async def _trail(org_id: uuid.UUID, contact_id: str) -> list[ActivityLog]:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        rows = (
            await session.execute(
                select(ActivityLog)
                .where(
                    ActivityLog.entity_type == "contact",
                    ActivityLog.entity_id == uuid.UUID(contact_id),
                )
                .order_by(ActivityLog.created_at.asc())
            )
        ).scalars().all()
    return list(rows)


async def _grant_client_role(client, headers, *, add: set[str]) -> None:
    roles = (await client.get("/api/v1/roles", headers=headers)).json()
    client_role = next(r for r in roles if r["key"] == "client")
    res = await client.patch(
        f"/api/v1/roles/{client_role['id']}",
        json={"permissions": sorted(set(client_role["permissions"]) | add)},
        headers=headers,
    )
    assert res.status_code == 200, res.text


async def test_impersonating_a_contact_runs_as_them_and_says_so(client_for) -> None:
    """The grant swaps the *effective* user; the session stays the staff member's.

    ``/meta/me`` is what the banner reads: it must name the client, name who is behind them, and
    say which kind of impersonation this is (the stop differs per kind).
    """
    tenant, headers, contact, _company, portal_user = await _portal_contact(
        client_for, "imp-happy"
    )

    async with client_for(tenant.host) as c:
        started = await c.post(
            f"/api/v1/contacts/{contact['id']}/portal/impersonate",
            json={"minutes": 30},
            headers=headers,
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["target_email"] == contact["email"]
        # Time-boxed, and clamped by SCHAKL_IMPERSONATION_MAX_MINUTES whatever was asked.
        expires_at = datetime.fromisoformat(body["expires_at"])
        assert expires_at <= datetime.now(UTC) + timedelta(
            minutes=settings.impersonation_max_minutes + 1
        )

        me = (await c.get("/api/v1/meta/me", headers=_both_cookies(headers, body["token"]))).json()
        assert me["email"] == contact["email"]
        assert me["impersonated_by"] == tenant.user.email
        assert me["impersonation_kind"] == "portal"
        assert me["is_portal"] is True
        # The effective permissions are the *client's*, never the impersonator's.
        assert "*" not in me["permissions"]
        assert me["is_instance_admin"] is False

        # …and the staff session on its own is still the staff member.
        assert (await c.get("/api/v1/meta/me", headers=headers)).json()["email"] == (
            tenant.user.email
        )

    trail = await _trail(tenant.org.id, contact["id"])
    started_rows = [row for row in trail if row.action == "portal_impersonation_started"]
    assert len(started_rows) == 1
    assert started_rows[0].actor_user_id == tenant.user.id
    assert started_rows[0].payload["email"] == contact["email"]
    assert portal_user is not None


async def test_a_grant_is_useless_to_anyone_else(client_for) -> None:
    """The grant names its impersonator; presented with another account's session it applies
    nothing, and the request simply runs as that account (never as the target)."""
    tenant, headers, contact, _company, _portal_user = await _portal_contact(
        client_for, "imp-bound"
    )
    other = await make_tenant("imp-bound-2")
    other_headers = await auth_cookie(other.user)

    async with client_for(tenant.host) as c:
        token = (
            await c.post(
                f"/api/v1/contacts/{contact['id']}/portal/impersonate",
                json={"minutes": 5},
                headers=headers,
            )
        ).json()["token"]

        # Another org's owner, on this host, carrying the stolen grant: they are not a member
        # here, so they are refused outright — and certainly not admitted as the contact.
        hijack = await c.get("/api/v1/meta/me", headers=_both_cookies(other_headers, token))
        assert hijack.status_code == 403
        # The grant alone, with no session at all, authenticates nothing.
        assert (
            await c.get("/api/v1/meta/me", headers={"Cookie": f"schakl_impersonate={token}"})
        ).status_code == 401


async def test_impersonation_needs_its_own_permission(client_for) -> None:
    """Managing the login (``members.member.write``) does not imply becoming the person."""
    tenant, headers, contact, _company, _portal_user = await _portal_contact(
        client_for, "imp-perm"
    )
    staff = await make_tenant("imp-perm-staff")  # unrelated tenant, used only for its user
    del staff

    async with client_for(tenant.host) as c:
        # A member holds neither; give them everything member management needs and nothing more.
        roles = (await c.get("/api/v1/roles", headers=headers)).json()
        member_role = next(r for r in roles if r["key"] == "member")
        await c.patch(
            f"/api/v1/roles/{member_role['id']}",
            json={
                "permissions": sorted(
                    set(member_role["permissions"])
                    | {"members.member.write", "contacts.contact.read"}
                )
            },
            headers=headers,
        )
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "medewerker-imp@example.com", "role": "member"},
            headers=headers,
        )
        assert invited.status_code in (200, 201), invited.text
        async with async_session_maker() as session:
            member_user = await session.scalar(
                select(User).where(User.email == "medewerker-imp@example.com")
            )
        member_headers = await auth_cookie(member_user)

        refused = await c.post(
            f"/api/v1/contacts/{contact['id']}/portal/impersonate",
            json={"minutes": 10},
            headers=member_headers,
        )
        assert refused.status_code == 403

        # Granting the permission is what opens it — and nothing else changed.
        await _grant_role(c, headers, "member", {IMPERSONATE})
        allowed = await c.post(
            f"/api/v1/contacts/{contact['id']}/portal/impersonate",
            json={"minutes": 10},
            headers=member_headers,
        )
        assert allowed.status_code == 200, allowed.text


async def _grant_role(client, headers, key: str, add: set[str]) -> None:
    roles = (await client.get("/api/v1/roles", headers=headers)).json()
    role = next(r for r in roles if r["key"] == key)
    res = await client.patch(
        f"/api/v1/roles/{role['id']}",
        json={"permissions": sorted(set(role["permissions"]) | add)},
        headers=headers,
    )
    assert res.status_code == 200, res.text


async def test_impersonation_may_never_gain_the_caller_a_permission(client_for) -> None:
    """A tenant can edit the ``client`` role freely, so "it's only a client" is not a bound on
    what that login holds. If the target holds something the caller doesn't, the answer is 403 —
    otherwise impersonation would be a privilege-escalation route with an audit trail."""
    tenant, headers, contact, _company, _portal_user = await _portal_contact(
        client_for, "imp-escalate"
    )

    async with client_for(tenant.host) as c:
        # A member who may impersonate, but may not read the team.
        await _grant_role(c, headers, "member", {IMPERSONATE, "contacts.contact.read"})
        await c.post(
            "/api/v1/members/invite",
            json={"email": "beperkt-imp@example.com", "role": "member"},
            headers=headers,
        )
        async with async_session_maker() as session:
            member_user = await session.scalar(
                select(User).where(User.email == "beperkt-imp@example.com")
            )
        member_headers = await auth_cookie(member_user)

        # Baseline: the client holds nothing extra, so it works.
        assert (
            await c.post(
                f"/api/v1/contacts/{contact['id']}/portal/impersonate",
                json={"minutes": 5},
                headers=member_headers,
            )
        ).status_code == 200

        # Now the client role holds a permission the member does not.
        await _grant_client_role(c, headers, add={"members.member.read"})
        refused = await c.post(
            f"/api/v1/contacts/{contact['id']}/portal/impersonate",
            json={"minutes": 5},
            headers=member_headers,
        )
        assert refused.status_code == 403
        assert refused.json()["error"]["message"] == "errors.impersonation_escalation"

        # The owner holds the wildcard, so the same call is fine for them.
        assert (
            await c.post(
                f"/api/v1/contacts/{contact['id']}/portal/impersonate",
                json={"minutes": 5},
                headers=headers,
            )
        ).status_code == 200


async def test_impersonation_cannot_be_nested(client_for) -> None:
    """An impersonated session may not open a second grant: it would launder one identity into
    another with only the first crossing recorded."""
    tenant, headers, contact, company, _portal_user = await _portal_contact(
        client_for, "imp-nest"
    )

    async with client_for(tenant.host) as c:
        second = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Truus",
                    "last_name": "Tweede",
                    "email": "truus-imp-nest@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        await c.post(f"/api/v1/contacts/{second['id']}/portal", headers=headers)
        # The client role is granted the permission (the owner holds it, so `covers` passes and
        # the grant is issued) — which is the only way to reach the nesting guard at all.
        await _grant_client_role(c, headers, add={IMPERSONATE})

        token = (
            await c.post(
                f"/api/v1/contacts/{contact['id']}/portal/impersonate",
                json={"minutes": 5},
                headers=headers,
            )
        ).json()["token"]

        nested = await c.post(
            f"/api/v1/contacts/{second['id']}/portal/impersonate",
            json={"minutes": 5},
            headers=_both_cookies(headers, token),
        )
        assert nested.status_code == 409
        assert nested.json()["error"]["message"] == "errors.impersonation_nested"


async def test_writes_made_while_impersonating_name_the_impersonator(client_for) -> None:
    """The point of the whole feature's audit half (§16).

    An impersonated request runs as the client — so without the impersonator on the row, the
    trail would read as if the client had done it. Both trails are checked: core's
    ``activity_log`` (via the stop, which is itself an impersonated write) and the tasks module's
    own ``task_activities`` (via a comment, the one thing a client portal login really writes).
    """
    tenant, headers, contact, company, _portal_user = await _portal_contact(
        client_for, "imp-trail"
    )

    async with client_for(tenant.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Zichtbaar voor klant",
                    "company_id": company["id"],
                    "visible_to_client": True,
                },
                headers=headers,
            )
        ).json()
        token = (
            await c.post(
                f"/api/v1/contacts/{contact['id']}/portal/impersonate",
                json={"minutes": 15},
                headers=headers,
            )
        ).json()["token"]
        as_client = _both_cookies(headers, token)

        commented = await c.post(
            f"/api/v1/tasks/{task['id']}/comments",
            json={"body": "Namens de klant gekeken"},
            headers=as_client,
        )
        assert commented.status_code == 201, commented.text

        # Staff read the task back: the comment's activity line names the client *and* who was
        # signed in as them — and so does the comment itself, which is the artifact anyone
        # actually reads.
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert detail["comments"][0]["author_name"] == "Piet Klant"
        assert detail["comments"][0]["impersonator_name"] == tenant.user.email
        commented_rows = [
            row for row in detail["activities"] if row["action"] == "commented"
        ]
        assert commented_rows, detail["activities"]
        # The actor is the client (their portal account's display name, taken from the contact);
        # the impersonator is the staff member who was signed in as them.
        assert commented_rows[0]["actor_name"] == "Piet Klant"
        assert commented_rows[0]["impersonator_name"] == tenant.user.email

        # An ordinary staff write carries no impersonator at all.
        await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"title": "Hernoemd"}, headers=headers
        )
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert all(
            row["impersonator_name"] is None
            for row in detail["activities"]
            if row["action"] == "updated"
        )

        stopped = await c.post(
            "/api/v1/contacts/portal/impersonation/stop", headers=as_client
        )
        assert stopped.status_code == 204
        # The grant cookie is cleared on the way out.
        assert "schakl_impersonate=" in stopped.headers.get("set-cookie", "")

    trail = await _trail(tenant.org.id, contact["id"])
    stop_rows = [row for row in trail if row.action == "portal_impersonation_stopped"]
    assert len(stop_rows) == 1
    # Recorded as the client (that is who the request ran as) *by* the staff member behind them.
    assert stop_rows[0].actor_user_id != tenant.user.id
    assert stop_rows[0].impersonator_user_id == tenant.user.id
    assert stop_rows[0].impersonator_name == tenant.user.email

    # And the feed the panel renders carries the name through.
    async with client_for(tenant.host) as c:
        feed = (
            await c.get(
                f"/api/v1/activity?entity_type=contact&entity_id={contact['id']}",
                headers=headers,
            )
        ).json()
    by_action = {row["action"]: row for row in feed}
    assert by_action["portal_impersonation_stopped"]["impersonator_name"] == (
        tenant.user.email
    )
    assert by_action["portal_enabled"]["impersonator_name"] is None


async def test_stop_is_idempotent_and_harmless_without_a_grant(client_for) -> None:
    """A stale tab or a double click must not error — and must not write a trail line either."""
    tenant, headers, contact, _company, _portal_user = await _portal_contact(
        client_for, "imp-stop-noop"
    )
    async with client_for(tenant.host) as c:
        assert (
            await c.post("/api/v1/contacts/portal/impersonation/stop", headers=headers)
        ).status_code == 204
    trail = await _trail(tenant.org.id, contact["id"])
    assert not [row for row in trail if row.action == "portal_impersonation_stopped"]


async def test_a_login_that_is_disabled_or_absent_cannot_be_entered(client_for) -> None:
    """Nothing to sign in as: a contact with no portal, and one whose portal was switched off."""
    tenant, headers, contact, company, _portal_user = await _portal_contact(
        client_for, "imp-absent"
    )
    async with client_for(tenant.host) as c:
        bare = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Geen",
                    "last_name": "Login",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        assert (
            await c.post(
                f"/api/v1/contacts/{bare['id']}/portal/impersonate",
                json={"minutes": 5},
                headers=headers,
            )
        ).status_code == 404

        await c.delete(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
        assert (
            await c.post(
                f"/api/v1/contacts/{contact['id']}/portal/impersonate",
                json={"minutes": 5},
                headers=headers,
            )
        ).status_code == 404


async def test_impersonation_is_tenant_scoped(client_for) -> None:
    """Golden Rule 1: another tenant's owner cannot reach this contact, on either host."""
    tenant, _headers, contact, _company, _portal_user = await _portal_contact(
        client_for, "imp-iso"
    )
    other = await make_tenant("imp-iso-other")
    other_headers = await auth_cookie(other.user)

    async with client_for(other.host) as c:
        assert (
            await c.post(
                f"/api/v1/contacts/{contact['id']}/portal/impersonate",
                json={"minutes": 5},
                headers=other_headers,
            )
        ).status_code == 404
    async with client_for(tenant.host) as c:
        # …and on the target's own host they are not a member at all.
        assert (
            await c.post(
                f"/api/v1/contacts/{contact['id']}/portal/impersonate",
                json={"minutes": 5},
                headers=other_headers,
            )
        ).status_code == 403


async def test_a_scoped_membership_can_only_enter_its_own_clients_contacts(client_for) -> None:
    """The company horizon (#191/#285) governs this like every other contact read: a contact at
    a client outside the horizon answers 404, so a group-scoped account manager cannot sign in
    as another manager's client."""
    tenant, headers, contact, _company, _portal_user = await _portal_contact(
        client_for, "imp-horizon"
    )
    async with client_for(tenant.host) as c:
        # A member who may impersonate, scoped to a company group that holds nothing.
        await _grant_role(c, headers, "member", {IMPERSONATE, "contacts.contact.read"})
        group = await c.post(
            "/api/v1/companies/groups", json={"name": "Alleen deze"}, headers=headers
        )
        assert group.status_code in (200, 201), group.text
        await c.post(
            "/api/v1/members/invite",
            json={"email": "scoped-imp@example.com", "role": "member"},
            headers=headers,
        )
        members = (await c.get("/api/v1/members", headers=headers)).json()
        scoped = next(m for m in members if m["email"] == "scoped-imp@example.com")
        assigned = await c.put(
            f"/api/v1/companies/groups/{group.json()['id']}/memberships",
            json={"membership_ids": [scoped["membership_id"]]},
            headers=headers,
        )
        assert assigned.status_code in (200, 204), assigned.text

        async with async_session_maker() as session:
            scoped_user = await session.scalar(
                select(User).where(User.email == "scoped-imp@example.com")
            )
        scoped_headers = await auth_cookie(scoped_user)

        refused = await c.post(
            f"/api/v1/contacts/{contact['id']}/portal/impersonate",
            json={"minutes": 5},
            headers=scoped_headers,
        )
        assert refused.status_code == 404, refused.text
