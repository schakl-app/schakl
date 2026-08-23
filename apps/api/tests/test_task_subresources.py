"""Task satellites: labels, checklists, comments, activity — CRUD, permissions, isolation."""

from __future__ import annotations

import uuid

from pwdlib import PasswordHash

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import FAR_FUTURE_DUE, Tenant, add_membership, auth_cookie, make_tenant

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
        await add_membership(session, tenant.org.id, user.id, role)
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
        task = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "T"},
            headers=headers,
        )).json()
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
        task = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "T"},
            headers=headers,
        )).json()
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


async def test_checklists_and_items_reorder(client_for) -> None:
    """One call sets a whole order, for the checklists of a task and the items of a checklist."""
    t = await make_tenant("checklist-order")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "T"},
            headers=headers,
        )).json()
        tid = task["id"]

        async def checklist(title: str) -> dict:
            return (
                await c.post(
                    f"/api/v1/tasks/{tid}/checklists", json={"title": title}, headers=headers
                )
            ).json()

        async def titles() -> list[str]:
            detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()
            return [cl["title"] for cl in detail["checklists"]]

        first, second, third = (
            await checklist("First"),
            await checklist("Second"),
            await checklist("Third"),
        )
        assert await titles() == ["First", "Second", "Third"]

        ordered = await c.post(
            f"/api/v1/tasks/{tid}/checklists/order",
            json={"checklist_ids": [third["id"], first["id"], second["id"]]},
            headers=headers,
        )
        assert ordered.status_code == 200
        assert ordered.json()["ids"] == [third["id"], first["id"], second["id"]]
        assert await titles() == ["Third", "First", "Second"]

        # An id the payload omits keeps its relative place *after* the named ones — a checklist
        # added in another tab mid-drag is appended, never dropped and never a 409.
        appended = await c.post(
            f"/api/v1/tasks/{tid}/checklists/order",
            json={"checklist_ids": [second["id"]]},
            headers=headers,
        )
        assert appended.json()["ids"] == [second["id"], third["id"], first["id"]]

        # Items of one checklist, same contract.
        base = f"/api/v1/tasks/{tid}/checklists/{first['id']}"
        items = [
            (await c.post(f"{base}/items", json={"title": title}, headers=headers)).json()
            for title in ("One", "Two", "Three")
        ]
        await c.post(
            f"{base}/items/order",
            json={"item_ids": [items[2]["id"], items[0]["id"], items[1]["id"]]},
            headers=headers,
        )
        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()
        listed = next(cl for cl in detail["checklists"] if cl["id"] == first["id"])
        assert [i["title"] for i in listed["items"]] == ["Three", "One", "Two"]

        # A reorder is not activity (the same rule as `position` on a task, #61).
        assert "updated" not in [a["action"] for a in detail["activities"]]


async def test_reorder_refuses_foreign_and_duplicate_ids(client_for) -> None:
    """An ordering call may not probe for rows it cannot see, and a repeat is a bad payload."""
    t = await make_tenant("checklist-order-guard")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        mine = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Mine"},
            headers=headers,
        )).json()
        other = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Other"},
            headers=headers,
        )).json()
        ours = (
            await c.post(
                f"/api/v1/tasks/{mine['id']}/checklists", json={"title": "A"}, headers=headers
            )
        ).json()
        theirs = (
            await c.post(
                f"/api/v1/tasks/{other['id']}/checklists", json={"title": "B"}, headers=headers
            )
        ).json()

        # Another task's checklist is a 404 — the answer reading it through this task gives.
        assert (
            await c.post(
                f"/api/v1/tasks/{mine['id']}/checklists/order",
                json={"checklist_ids": [theirs["id"]]},
                headers=headers,
            )
        ).status_code == 404
        assert (
            await c.post(
                f"/api/v1/tasks/{mine['id']}/checklists/order",
                json={"checklist_ids": [ours["id"], ours["id"]]},
                headers=headers,
            )
        ).status_code == 422
        # An empty order says nothing; the schema refuses it rather than storing a no-op.
        assert (
            await c.post(
                f"/api/v1/tasks/{mine['id']}/checklists/order",
                json={"checklist_ids": []},
                headers=headers,
            )
        ).status_code == 422

        # An item of another checklist is a 404 too, not a silent no-op.
        item = (
            await c.post(
                f"/api/v1/tasks/{other['id']}/checklists/{theirs['id']}/items",
                json={"title": "X"},
                headers=headers,
            )
        ).json()
        assert (
            await c.post(
                f"/api/v1/tasks/{mine['id']}/checklists/{ours['id']}/items/order",
                json={"item_ids": [item["id"]]},
                headers=headers,
            )
        ).status_code == 404


async def test_duplicate_checklist(client_for) -> None:
    """A copy carries the items and their descriptions, drops the ticks, and lands directly
    under its source — never at the end, behind whatever was added since."""
    t = await make_tenant("checklist-dup")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "T"},
            headers=headers,
        )).json()
        source = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/checklists",
                json={"title": "Launch", "description": "Elke release"},
                headers=headers,
            )
        ).json()
        base = f"/api/v1/tasks/{task['id']}/checklists/{source['id']}"
        item = (
            await c.post(
                f"{base}/items", json={"title": "One", "description": "Hoe"}, headers=headers
            )
        ).json()
        await c.post(f"{base}/items", json={"title": "Two"}, headers=headers)
        await c.patch(f"{base}/items/{item['id']}", json={"done": True}, headers=headers)
        # A third checklist added after the source: the copy must slot between them.
        await c.post(
            f"/api/v1/tasks/{task['id']}/checklists", json={"title": "Aftercare"}, headers=headers
        )

        created = await c.post(f"{base}/duplicate", json={"title": "Launch 2"}, headers=headers)
        assert created.status_code == 201
        copy = created.json()
        assert copy["id"] != source["id"]
        assert copy["title"] == "Launch 2"
        assert copy["description"] == "Elke release"
        # The POST answers with the items it just wrote, unticked.
        assert [(i["title"], i["description"], i["done"]) for i in copy["items"]] == [
            ("One", "Hoe", False),
            ("Two", None, False),
        ]

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert [cl["title"] for cl in detail["checklists"]] == ["Launch", "Launch 2", "Aftercare"]
        # The source keeps its own ticks: a duplicate reads nothing back onto it.
        assert [i["done"] for i in detail["checklists"][0]["items"]] == [True, False]
        assert any(a["action"] == "checklist_duplicated" for a in detail["activities"])

        # Omitted title reuses the source's — the API invents no "(kopie)" (CLAUDE.md §2).
        same_name = (await c.post(f"{base}/duplicate", json={}, headers=headers)).json()
        assert same_name["title"] == "Launch"

        # A checklist belonging to another task is a 404, not somebody else's copy.
        other = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Other"},
            headers=headers,
        )).json()
        stray = await c.post(
            f"/api/v1/tasks/{other['id']}/checklists/{source['id']}/duplicate",
            json={},
            headers=headers,
        )
        assert stray.status_code == 404


async def test_comments_permissions_and_activity(client_for) -> None:
    t = await make_tenant("comments")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t, name="Milo Member")
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"due_date": FAR_FUTURE_DUE, "title": "T"},
                headers=owner_headers,
            )
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
        task = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "T"},
            headers=headers,
        )).json()
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
        task = (await ca.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "S"},
            headers=a_headers,
        )).json()
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
