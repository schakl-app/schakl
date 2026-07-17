"""Company groups / the per-membership company data horizon (issue #191).

The third authorization axis: a restricted membership sees only the union of its groups'
companies — across every company-rooted module — while a membership with no assignments
(and every owner) keeps seeing everything.
"""

from __future__ import annotations

import uuid

from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant


async def _setup(client_for, slug: str):
    """An owner, a plain member, two companies, and one group holding only company A."""
    t = await make_tenant(slug)
    member = await make_tenant(f"{slug}-m", email=f"member-{slug}@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, member.user.id, role="member")
        membership_id = membership.id
        await session.commit()
    membership = type("M", (), {"id": membership_id})()
    owner_headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member.user)

    async with client_for(t.host) as c:
        company_a = (
            await c.post("/api/v1/companies", json={"name": "Alpha"}, headers=owner_headers)
        ).json()
        company_b = (
            await c.post("/api/v1/companies", json={"name": "Beta"}, headers=owner_headers)
        ).json()
        group = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Team Noord"}, headers=owner_headers
            )
        ).json()
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/companies",
                json={"company_ids": [company_a["id"]]},
                headers=owner_headers,
            )
        ).status_code == 204
    return t, member, membership, owner_headers, member_headers, company_a, company_b, group


async def test_restricted_member_sees_only_their_groups_companies(client_for) -> None:
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz")

    async with client_for(t.host) as c:
        # Unassigned: the member sees all companies — fully backwards compatible.
        listed_all = (await c.get("/api/v1/companies", headers=member_h)).json()["items"]
        names = {r["name"] for r in listed_all}
        assert names == {"Alpha", "Beta"}

        # Assign the membership to the group holding only Alpha.
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204

        listed = (await c.get("/api/v1/companies", headers=member_h)).json()
        assert {r["name"] for r in listed["items"]} == {"Alpha"}

        # Get-by-id outside the horizon reads as 404 — never 403 (existence must not leak).
        assert (await c.get(f"/api/v1/companies/{b['id']}", headers=member_h)).status_code == 404
        assert (await c.get(f"/api/v1/companies/{a['id']}", headers=member_h)).status_code == 200

        # The owner is never restricted, whatever rows exist.
        owner_names = {
            r["name"] for r in (await c.get("/api/v1/companies", headers=owner_h)).json()["items"]
        }
        assert owner_names == {"Alpha", "Beta"}


async def test_horizon_filters_company_rooted_modules(client_for) -> None:
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz-mod")

    async with client_for(t.host) as c:
        # Rows on both companies, one company-less row.
        for company, title in ((a, "Task A"), (b, "Task B"), (None, "Task loose")):
            body = {"title": title}
            if company:
                body["company_id"] = company["id"]
            assert (
                await c.post("/api/v1/tasks", json=body, headers=owner_h)
            ).status_code == 201, title
        contact = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Bea", "company_ids": [b["id"]]},
            headers=owner_h,
        )
        assert contact.status_code == 201, contact.text
        project_b = await c.post(
            "/api/v1/projects", json={"name": "Proj B", "company_id": b["id"]}, headers=owner_h
        )
        assert project_b.status_code == 201, project_b.text

        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204

        # Tasks: the horizon admits Alpha's and the company-less row, never Beta's.
        titles = {
            r["title"]
            for r in (await c.get("/api/v1/tasks?limit=50", headers=member_h)).json()["items"]
        }
        assert titles == {"Task A", "Task loose"}

        # Projects on Beta are invisible.
        projects = (await c.get("/api/v1/projects?limit=50", headers=member_h)).json()["items"]
        assert all(p["company_id"] != b["id"] for p in projects)

        # Writes are scoped too: creating a task on an invisible company reads as 404.
        refused = await c.post(
            "/api/v1/tasks", json={"title": "X", "company_id": b["id"]}, headers=member_h
        )
        assert refused.status_code == 404
        # …and so is moving one there — even the member's own task (the ownership rule would
        # otherwise allow the write; the horizon still refuses the destination).
        mine = await c.post(
            "/api/v1/tasks",
            json={
                "title": "Mine",
                "company_id": a["id"],
                # `own` for a task is the assignee (§15), so assign it to the member.
                "assignee_user_id": str(member.user.id),
            },
            headers=member_h,
        )
        assert mine.status_code == 201, mine.text
        moved = await c.patch(
            f"/api/v1/tasks/{mine.json()['id']}", json={"company_id": b["id"]}, headers=member_h
        )
        assert moved.status_code == 404


async def test_membership_in_empty_group_sees_nothing(client_for) -> None:
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz-mt")

    async with client_for(t.host) as c:
        empty = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Leeg"}, headers=owner_h
            )
        ).json()
        assert (
            await c.put(
                f"/api/v1/companies/groups/{empty['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204
        # Assigned to a group with no companies = an empty horizon, not an unrestricted one.
        assert (await c.get("/api/v1/companies", headers=member_h)).json()["items"] == []


async def test_deleting_group_widens_visibility(client_for) -> None:
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz-del")

    async with client_for(t.host) as c:
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204
        assert len((await c.get("/api/v1/companies", headers=member_h)).json()["items"]) == 1

        # Deleting the group deletes its assignments: back to unrestricted, never broken.
        assert (
            await c.delete(f"/api/v1/companies/groups/{group['id']}", headers=owner_h)
        ).status_code == 204
        assert len((await c.get("/api/v1/companies", headers=member_h)).json()["items"]) == 2


async def test_group_management_requires_permission_and_isolates_tenants(client_for) -> None:
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz-iso")
    other = await make_tenant("horiz-iso-other")
    other_headers = await auth_cookie(other.user)

    async with client_for(t.host) as c:
        # A plain member holds no companies.group.manage.
        assert (await c.get("/api/v1/companies/groups", headers=member_h)).status_code == 403

    async with client_for(other.host) as c:
        # The other tenant sees no groups, and cannot touch this tenant's by id.
        assert (await c.get("/api/v1/companies/groups", headers=other_headers)).json() == []
        assert (
            await c.delete(f"/api/v1/companies/groups/{group['id']}", headers=other_headers)
        ).status_code == 404
        # A cross-tenant company id never sticks to the other tenant's group.
        own = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Eigen"}, headers=other_headers
            )
        ).json()
        assert (
            await c.put(
                f"/api/v1/companies/groups/{own['id']}/companies",
                json={"company_ids": [a["id"]]},
                headers=other_headers,
            )
        ).status_code == 204
        groups = (await c.get("/api/v1/companies/groups", headers=other_headers)).json()
        assert groups[0]["company_ids"] == []


async def test_horizon_records_activity(client_for) -> None:
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz-act")

    async with client_for(t.host) as c:
        assert (
            await c.patch(
                f"/api/v1/companies/groups/{group['id']}",
                json={"name": "Team Zuid"},
                headers=owner_h,
            )
        ).status_code == 200
        trail = (
            await c.get(
                f"/api/v1/activity?entity_type=company_group&entity_id={group['id']}",
                headers=owner_h,
            )
        ).json()
        actions = {row["action"] for row in (trail if isinstance(trail, list) else trail["items"])}
        assert {"created", "updated", "companies_changed"} <= actions


async def test_unknown_membership_id_is_ignored(client_for) -> None:
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz-uk")
    async with client_for(t.host) as c:
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(uuid.uuid4())]},
                headers=owner_h,
            )
        ).status_code == 204
        groups = (await c.get("/api/v1/companies/groups", headers=owner_h)).json()
        assert groups[0]["membership_ids"] == []


async def test_company_logo_upload_serve_and_horizon(client_for, tmp_path, monkeypatch) -> None:
    """Per-client logo (#196): upload/replace/remove via StoredFile, served tenant- and
    horizon-scoped — a restricted member never fetches an invisible company's logo, not even
    by raw file id."""
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "logo")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64

    async with client_for(t.host) as c:
        # Upload onto Beta; the company row now references the stored file.
        uploaded = await c.post(
            f"/api/v1/companies/{b['id']}/logo",
            files={"file": ("logo.png", png, "image/png")},
            headers=owner_h,
        )
        assert uploaded.status_code == 200, uploaded.text
        logo_id = uploaded.json()["logo_file_id"]
        assert logo_id

        served = await c.get(f"/api/v1/companies/{b['id']}/logo", headers=owner_h)
        assert served.status_code == 200 and served.content == png

        # Replace: a second upload swaps the file and cleans the old row up.
        replaced = await c.post(
            f"/api/v1/companies/{b['id']}/logo",
            files={"file": ("logo2.png", png, "image/png")},
            headers=owner_h,
        )
        assert replaced.json()["logo_file_id"] != logo_id
        assert (await c.get(f"/api/v1/files/{logo_id}", headers=owner_h)).status_code == 404
        logo_id = replaced.json()["logo_file_id"]

        # Restrict the member to Alpha only: Beta's logo is invisible through every path.
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204
        assert (
            await c.get(f"/api/v1/companies/{b['id']}/logo", headers=member_h)
        ).status_code == 404
        # …including the generic file route, by raw id.
        assert (await c.get(f"/api/v1/files/{logo_id}", headers=member_h)).status_code == 404
        # The owner still sees it, and non-images are refused.
        assert (
            await c.get(f"/api/v1/companies/{b['id']}/logo", headers=owner_h)
        ).status_code == 200
        refused = await c.post(
            f"/api/v1/companies/{b['id']}/logo",
            files={"file": ("x.txt", b"hi", "text/plain")},
            headers=owner_h,
        )
        assert refused.status_code == 422

        # Remove: the reference clears and the trail carries the change.
        removed = await c.delete(f"/api/v1/companies/{b['id']}/logo", headers=owner_h)
        assert removed.json()["logo_file_id"] is None
        trail = (
            await c.get(
                f"/api/v1/activity?entity_type=company&entity_id={b['id']}",
                headers=owner_h,
            )
        ).json()
        actions = {row["action"] for row in trail}
        assert {"logo_uploaded", "logo_removed"} <= actions


async def test_company_logo_is_tenant_scoped(client_for, tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "logo-iso")
    other = await make_tenant("logo-iso-other")
    other_h = await auth_cookie(other.user)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64

    async with client_for(t.host) as c:
        await c.post(
            f"/api/v1/companies/{a['id']}/logo",
            files={"file": ("logo.png", png, "image/png")},
            headers=owner_h,
        )
    async with client_for(other.host) as c:
        assert (
            await c.get(f"/api/v1/companies/{a['id']}/logo", headers=other_h)
        ).status_code == 404
