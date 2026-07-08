"""Task satellites: labels, checklists, comments, activity — CRUD, permissions, isolation."""

from __future__ import annotations

import uuid

from pwdlib import PasswordHash

from app.core.auth.models import User
from app.core.models import Membership
from app.db import async_session_maker, set_current_org
from tests.conftest import Tenant, auth_cookie, make_tenant

_password_hash = PasswordHash.recommended()


async def add_member(tenant: Tenant, *, role: str = "member", name: str | None = None) -> User:
    """A second user in the same org (make_tenant always creates a fresh org)."""
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=f"{uuid.uuid4().hex[:10]}@example.com",
            full_name=name,
            hashed_password=_password_hash.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, tenant.org.id)
        session.add(Membership(org_id=tenant.org.id, user_id=user.id, role=role))
        await session.commit()
        return User(id=user.id, email=user.email, hashed_password="", is_active=True)


async def test_labels_crud_and_unique_name(client_for) -> None:
    t = await make_tenant("label-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/tasks/labels", json={"name": "SEO", "color": "emerald"}, headers=headers
        )
        assert created.status_code == 201
        label = created.json()

        dup = await c.post(
            "/api/v1/tasks/labels", json={"name": "SEO", "color": "red"}, headers=headers
        )
        assert dup.status_code == 409

        patched = await c.patch(
            f"/api/v1/tasks/labels/{label['id']}", json={"color": "amber"}, headers=headers
        )
        assert patched.json()["color"] == "amber"

        assert len((await c.get("/api/v1/tasks/labels", headers=headers)).json()) == 1
        assert (
            await c.delete(f"/api/v1/tasks/labels/{label['id']}", headers=headers)
        ).status_code == 204


async def test_set_task_labels_and_list_aggregates(client_for) -> None:
    t = await make_tenant("label-set")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        l1 = (
            await c.post(
                "/api/v1/tasks/labels", json={"name": "A", "color": "red"}, headers=headers
            )
        ).json()
        l2 = (
            await c.post(
                "/api/v1/tasks/labels", json={"name": "B", "color": "blue"}, headers=headers
            )
        ).json()

        put = await c.put(
            f"/api/v1/tasks/{task['id']}/labels",
            json={"label_ids": [l1["id"], l2["id"]]},
            headers=headers,
        )
        assert put.status_code == 200
        assert {row["name"] for row in put.json()} == {"A", "B"}

        # Replace the set with just one label.
        put = await c.put(
            f"/api/v1/tasks/{task['id']}/labels",
            json={"label_ids": [l2["id"]]},
            headers=headers,
        )
        assert [row["name"] for row in put.json()] == ["B"]

        # List rows carry the chips.
        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        assert [label["name"] for label in listed[0]["labels"]] == ["B"]


async def test_checklists_and_items(client_for) -> None:
    t = await make_tenant("checklist")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        checklist = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/checklists",
                json={"title": "Launch"},
                headers=headers,
            )
        ).json()

        base = f"/api/v1/tasks/{task['id']}/checklists/{checklist['id']}"
        item1 = (await c.post(f"{base}/items", json={"title": "One"}, headers=headers)).json()
        await c.post(f"{base}/items", json={"title": "Two"}, headers=headers)

        toggled = await c.patch(
            f"{base}/items/{item1['id']}", json={"done": True}, headers=headers
        )
        assert toggled.json()["done"] is True

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert detail["checklists"][0]["title"] == "Launch"
        assert [i["title"] for i in detail["checklists"][0]["items"]] == ["One", "Two"]

        # Aggregates on the list row.
        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        assert (listed[0]["checklist_done"], listed[0]["checklist_total"]) == (1, 2)

        assert (
            await c.delete(f"{base}/items/{item1['id']}", headers=headers)
        ).status_code == 204
        assert (await c.delete(base, headers=headers)).status_code == 204


async def test_comments_permissions_and_activity(client_for) -> None:
    t = await make_tenant("comments")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t, name="Milo Member")
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        task = (
            await c.post("/api/v1/tasks", json={"title": "T"}, headers=owner_headers)
        ).json()
        comment = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "First!"},
                headers=member_headers,
            )
        ).json()
        assert comment["author_name"] == "Milo Member"
        assert comment["edited_at"] is None

        # Author edits their own comment.
        edited = await c.patch(
            f"/api/v1/tasks/{task['id']}/comments/{comment['id']}",
            json={"body": "Edited"},
            headers=member_headers,
        )
        assert edited.json()["edited_at"] is not None

        # Someone else (even the owner) cannot edit it…
        assert (
            await c.patch(
                f"/api/v1/tasks/{task['id']}/comments/{comment['id']}",
                json={"body": "Hijack"},
                headers=owner_headers,
            )
        ).status_code == 403
        # …but a manager may delete it.
        assert (
            await c.delete(
                f"/api/v1/tasks/{task['id']}/comments/{comment['id']}",
                headers=owner_headers,
            )
        ).status_code == 204

        # Activity feed recorded creation, status changes and the comment.
        await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=owner_headers
        )
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers)).json()
        actions = [a["action"] for a in detail["activities"]]
        assert "created" in actions
        assert "commented" in actions
        assert "status_changed" in actions


async def test_inline_edits_and_deletes_land_in_activity(client_for) -> None:
    """Editing/deleting a comment and deleting a link/checklist/item is audited (UX.md)."""
    t = await make_tenant("inline-activity")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        tid = task["id"]

        comment = (
            await c.post(f"/api/v1/tasks/{tid}/comments", json={"body": "Hi"}, headers=headers)
        ).json()
        await c.patch(
            f"/api/v1/tasks/{tid}/comments/{comment['id']}", json={"body": "Hi!"}, headers=headers
        )
        await c.delete(f"/api/v1/tasks/{tid}/comments/{comment['id']}", headers=headers)

        link = (
            await c.post(
                f"/api/v1/tasks/{tid}/links",
                json={"url": "example.com", "title": "Brief"},
                headers=headers,
            )
        ).json()
        await c.delete(f"/api/v1/tasks/{tid}/links/{link['id']}", headers=headers)

        checklist = (
            await c.post(
                f"/api/v1/tasks/{tid}/checklists", json={"title": "Launch"}, headers=headers
            )
        ).json()
        base = f"/api/v1/tasks/{tid}/checklists/{checklist['id']}"
        item = (await c.post(f"{base}/items", json={"title": "One"}, headers=headers)).json()
        await c.delete(f"{base}/items/{item['id']}", headers=headers)
        await c.delete(base, headers=headers)

        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()
        actions = [a["action"] for a in detail["activities"]]
        for expected in (
            "comment_edited",
            "comment_deleted",
            "link_deleted",
            "checklist_item_deleted",
            "checklist_deleted",
        ):
            assert expected in actions, f"{expected} missing from activity feed"

        # The delete entries carry the human-readable title/url for the feed text.
        link_entry = next(a for a in detail["activities"] if a["action"] == "link_deleted")
        assert link_entry["payload"]["title"] == "Brief"


async def test_subresources_tenant_isolation(client_for) -> None:
    a = await make_tenant("sub-iso-a")
    b = await make_tenant("sub-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        task = (await ca.post("/api/v1/tasks", json={"title": "S"}, headers=a_headers)).json()
        label = (
            await ca.post(
                "/api/v1/tasks/labels", json={"name": "L", "color": "red"}, headers=a_headers
            )
        ).json()
        checklist = (
            await ca.post(
                f"/api/v1/tasks/{task['id']}/checklists",
                json={"title": "C"},
                headers=a_headers,
            )
        ).json()

    async with client_for(b.host) as cb:
        # Nothing of tenant A is visible or writable through nested paths.
        assert (await cb.get("/api/v1/tasks/labels", headers=b_headers)).json() == []
        assert (
            await cb.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "spy"},
                headers=b_headers,
            )
        ).status_code == 404
        assert (
            await cb.post(
                f"/api/v1/tasks/{task['id']}/checklists/{checklist['id']}/items",
                json={"title": "spy"},
                headers=b_headers,
            )
        ).status_code == 404
        assert (
            await cb.put(
                f"/api/v1/tasks/{task['id']}/labels",
                json={"label_ids": [label["id"]]},
                headers=b_headers,
            )
        ).status_code == 404
        assert (
            await cb.delete(f"/api/v1/tasks/labels/{label['id']}", headers=b_headers)
        ).status_code == 404

    # Literal routes must not be shadowed by /tasks/{task_id} (route-order trap).
    async with client_for(a.host) as ca:
        assert (await ca.get("/api/v1/tasks/templates", headers=a_headers)).status_code == 200
