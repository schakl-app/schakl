"""The seeded roles' posture on companies, contacts and projects (issue #19, #51).

The deliberate behaviour change of the epic: a `member` reads these three modules and writes
none of them. An `admin` writes all three. Both halves are asserted, because a permission model
that only proves the negative would pass with every write endpoint broken.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _company(client, headers, name: str = "Acme") -> str:
    created = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def test_admin_can_write_all_three_modules(client_for) -> None:
    tenant = await make_tenant("rbac-admin-write", role="admin")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)

        contact = await client.post(
            "/api/v1/contacts", json={"first_name": "Ada", "last_name": "L"}, headers=headers
        )
        assert contact.status_code == 201
        contact_id = contact.json()["id"]

        linked = await client.post(
            f"/api/v1/contacts/{contact_id}/links",
            json={"company_id": company_id},
            headers=headers,
        )
        assert linked.status_code == 201

        project = await client.post(
            "/api/v1/projects",
            json={"name": "Website", "company_id": company_id},
            headers=headers,
        )
        assert project.status_code == 201

        assert (
            await client.delete(f"/api/v1/projects/{project.json()['id']}", headers=headers)
        ).status_code == 204
        assert (
            await client.delete(f"/api/v1/contacts/{contact_id}", headers=headers)
        ).status_code == 204
        assert (
            await client.delete(f"/api/v1/companies/{company_id}", headers=headers)
        ).status_code == 204


async def test_member_reads_but_cannot_write_companies_contacts_projects(client_for) -> None:
    tenant = await make_tenant("rbac-member-ro", role="admin")
    admin_headers = await auth_cookie(tenant.user)

    import uuid

    from app.core.auth.models import User
    from app.db import async_session_maker, set_current_org
    from tests.conftest import add_membership

    async with async_session_maker() as session:
        member = User(
            id=uuid.uuid4(), email="m@example.com", hashed_password="", is_active=True,
            is_verified=True,
        )
        session.add(member)
        await session.flush()
        await set_current_org(session, tenant.org.id)
        await add_membership(session, tenant.org.id, member.id, "member")
        await session.commit()
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        company_id = await _company(client, admin_headers, "Readable")

        # Reads: yes.
        assert (await client.get("/api/v1/companies", headers=member_headers)).status_code == 200
        assert (
            await client.get(f"/api/v1/companies/{company_id}", headers=member_headers)
        ).status_code == 200
        assert (await client.get("/api/v1/contacts", headers=member_headers)).status_code == 200
        assert (await client.get("/api/v1/projects", headers=member_headers)).status_code == 200
        assert (
            await client.get(f"/api/v1/companies/{company_id}/panels", headers=member_headers)
        ).status_code == 200

        # Writes: no. (docs/DEPLOY.md documents this as the upgrade's behaviour change.)
        assert (
            await client.post("/api/v1/companies", json={"name": "X"}, headers=member_headers)
        ).status_code == 403
        assert (
            await client.patch(
                f"/api/v1/companies/{company_id}", json={"name": "Y"}, headers=member_headers
            )
        ).status_code == 403
        assert (
            await client.delete(f"/api/v1/companies/{company_id}", headers=member_headers)
        ).status_code == 403
        assert (
            await client.post(
                "/api/v1/contacts", json={"first_name": "Nope"}, headers=member_headers
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/v1/projects",
                json={"name": "Nope", "company_id": company_id},
                headers=member_headers,
            )
        ).status_code == 403


async def test_a_client_role_reads_the_three_modules_and_writes_nothing(client_for) -> None:
    tenant = await make_tenant("rbac-client-ro", role="client")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        assert (await client.get("/api/v1/companies", headers=headers)).status_code == 200
        assert (
            await client.post("/api/v1/companies", json={"name": "X"}, headers=headers)
        ).status_code == 403
