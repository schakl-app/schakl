"""An employee keeps the address book current without asking an admin first (#310).

Two failures met here, and only the first is about a default. A `member` held neither contact
write, so adding the new marketing person at a client was a 403 — and granting the permission
whose label says exactly that (`contacts.contact.write`, *Contactpersonen aanmaken en bewerken*)
still refused the flow people actually use, because creating a contact **at a client** attaches it
in the same call and attaching is `contacts.link.write`. The web drew every one of those controls
behind the first key while calling the second, so the screen offered a button the API refused.

The second half is what the negative test below pins: the API is right to demand both, so the
defaults grant both and the UI mirrors both.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.models import OrgSettings
from app.core.permissions.models import Role, RolePermission
from app.core.permissions.reconcile import REVISIONS, reconcile_org
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member

_MARKER = "@rev:310-contacts-member-write"
_WRITES = ("contacts.contact.write", "contacts.link.write")


async def _set_member_permissions(c, headers, permissions: set[str]) -> None:
    """Rewrite the seeded `member` role to exactly `permissions` (plus what it must keep)."""
    roles = (await c.get("/api/v1/roles", headers=headers)).json()
    member = next(r for r in roles if r["key"] == "member")
    keep = {p for p in member["permissions"] if not p.startswith("contacts.")}
    res = await c.patch(
        f"/api/v1/roles/{member['id']}",
        json={"permissions": sorted(keep | permissions)},
        headers=headers,
    )
    assert res.status_code == 200, res.text


async def test_member_manages_contacts_without_a_grant(client_for) -> None:
    """The reported defect: a Medewerker adds and edits contact people out of the box."""
    t = await make_tenant("contacts-member-default")
    owner = await auth_cookie(t.user)
    employee = await auth_cookie(await add_member(t, role="member"))

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=owner)
        ).json()["id"]

        # Created at a client, which is the only way the client page can do it — one call that
        # writes the contact *and* attaches it, so it needs both defaults at once.
        created = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Ada", "last_name": "Bakker", "company_ids": [company]},
            headers=employee,
        )
        assert created.status_code == 201, created.text
        contact = created.json()
        assert [link["company_id"] for link in contact["companies"]] == [company]

        edited = await c.patch(
            f"/api/v1/contacts/{contact['id']}",
            json={"job_title": "Marketing"},
            headers=employee,
        )
        assert edited.status_code == 200, edited.text

        # Detaching and re-attaching an existing person: the same key, the other direction.
        assert (
            await c.delete(
                f"/api/v1/contacts/{contact['id']}/links/{company}", headers=employee
            )
        ).status_code == 204
        relinked = await c.post(
            f"/api/v1/contacts/{contact['id']}/links",
            json={"company_id": company},
            headers=employee,
        )
        assert relinked.status_code == 201, relinked.text

        # Deleting a person is still an admin's call: it takes their portal login and the
        # counterpart of every contact moment with them.
        assert (
            await c.delete(f"/api/v1/contacts/{contact['id']}", headers=employee)
        ).status_code == 403


async def test_write_without_link_write_still_refuses_to_attach(client_for) -> None:
    """Why the two defaults travel together, and why the web must gate on both.

    A tenant may still take `contacts.link.write` away — and then every attaching control has to
    be *absent*, not merely refused, because this 403 names neither permission.
    """
    t = await make_tenant("contacts-write-only")
    owner = await auth_cookie(t.user)
    employee = await auth_cookie(await add_member(t, role="member"))

    async with client_for(t.host) as c:
        await _set_member_permissions(
            c, owner, {"contacts.contact.read", "contacts.contact.write"}
        )
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=owner)
        ).json()["id"]

        # A contact of their own: fine. The same contact at a client: refused before the row is
        # written, so nothing is created and rolled back under them.
        plain = await c.post("/api/v1/contacts", json={"first_name": "Bo"}, headers=employee)
        assert plain.status_code == 201, plain.text
        attached = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Cy", "company_ids": [company]},
            headers=employee,
        )
        assert attached.status_code == 403
        assert (
            await c.post(
                f"/api/v1/contacts/{plain.json()['id']}/links",
                json={"company_id": company},
                headers=employee,
            )
        ).status_code == 403
        assert (await c.get("/api/v1/contacts", headers=employee)).json()["total"] == 1


async def test_reconciler_widens_an_existing_org(client_for) -> None:
    """An org seeded before #310 gets both writes on the next boot.

    The key diff cannot see this — every org was already offered both keys as admin-only — so it
    is a `DefaultsRevision`, and it runs exactly once (CLAUDE.md §15).
    """
    marker = next(r for r in REVISIONS if r.marker == _MARKER).marker
    t = await make_tenant("contacts-rev310")

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # Rewind to a pre-#310 org: admin was offered both writes and holds them, member holds
        # neither, and the revision has never run.
        member_role = await session.scalar(
            select(Role.id).where(Role.org_id == t.org.id, Role.key == "member")
        )
        await session.execute(
            RolePermission.__table__.delete().where(
                RolePermission.org_id == t.org.id,
                RolePermission.role_id == member_role,
                RolePermission.permission.in_(list(_WRITES)),
            )
        )
        org_settings = await session.scalar(
            select(OrgSettings).where(OrgSettings.org_id == t.org.id)
        )
        org_settings.applied_permission_defaults = [
            k for k in (org_settings.applied_permission_defaults or ()) if k != marker
        ]
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        org = await session.get(type(t.org), t.org.id)
        await reconcile_org(org, session)
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        held = {
            (role_key, permission)
            for role_key, permission in (
                await session.execute(
                    select(Role.key, RolePermission.permission)
                    .join(RolePermission, RolePermission.role_id == Role.id)
                    .where(
                        Role.org_id == t.org.id,
                        RolePermission.permission.in_(list(_WRITES)),
                    )
                )
            ).all()
        }
        assert {("member", p) for p in _WRITES} <= held
        assert {("admin", p) for p in _WRITES} <= held  # untouched by the revision
        # Recorded, so a tenant who later unticks one is not handed it back on every boot.
        org_settings = await session.scalar(
            select(OrgSettings).where(OrgSettings.org_id == t.org.id)
        )
        assert marker in org_settings.applied_permission_defaults
