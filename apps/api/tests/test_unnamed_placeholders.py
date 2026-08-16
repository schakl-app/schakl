"""A row nobody named says so, and naming it stops it saying so (#350).

Create-then-edit (#230) creates the record first and lands the user on its detail page in edit
mode. When they never finish, the placeholder title survives — and a placeholder *is* an ordinary
title, so eight of eleven open tasks on the dev database read as real work. Worse, the
placeholder was written in the **creator's** locale, so one org held both "Naamloze taak" and
"Untitled task", alphabetised into two clumps no search could gather.

The flag is what a title could never be: a fact about the row rather than a string in it. These
pin the three things that makes possible — marking, clearing, and finding.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _company(c, headers) -> str:
    return (await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)).json()[
        "id"
    ]


# ------------------------------------------------------------------------------ tasks


async def test_an_ordinary_create_is_never_unnamed(client_for) -> None:
    """A caller who supplies a title has named the thing; the flag is opt-in, not inferred."""
    t = await make_tenant("unnamed-task-plain")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        created = await c.post("/api/v1/tasks", json={"title": "Homepage herzien"}, headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["unnamed"] is False


async def test_a_create_then_edit_task_is_marked_and_findable(client_for) -> None:
    t = await make_tenant("unnamed-task-flag")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await c.post("/api/v1/tasks", json={"title": "Echte taak"}, headers=headers)
        placeholder = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Naamloze taak", "unnamed": True},
                headers=headers,
            )
        ).json()
        assert placeholder["unnamed"] is True

        # The whole point: gathering them. Without the flag the only handle is a literal string
        # in whichever locale the creator happened to be using.
        only = (await c.get("/api/v1/tasks?unnamed=true", headers=headers)).json()
        assert [item["id"] for item in only["items"]] == [placeholder["id"]]
        assert only["total"] == 1

        named = (await c.get("/api/v1/tasks?unnamed=false", headers=headers)).json()
        assert placeholder["id"] not in [item["id"] for item in named["items"]]

        both = (await c.get("/api/v1/tasks", headers=headers)).json()
        assert both["total"] == 2, "omitting the filter must not narrow the list"


async def test_naming_a_task_clears_the_flag(client_for) -> None:
    """Enforced by the service, never asked of the caller: no write path may set a real title
    and leave the row filed under "nobody named this"."""
    t = await make_tenant("unnamed-task-clear")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = (
            await c.post(
                "/api/v1/tasks", json={"title": "Naamloze taak", "unnamed": True}, headers=headers
            )
        ).json()

        # An edit that touches anything else leaves it alone — it is still unnamed.
        other = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"priority": "high"}, headers=headers
        )
        assert other.json()["unnamed"] is True

        renamed = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"title": "Homepage herzien"}, headers=headers
        )
        assert renamed.json()["unnamed"] is False


async def test_a_task_update_cannot_set_the_flag(client_for) -> None:
    """`unnamed` is a create-time fact. A caller who could set it later could hide real work."""
    t = await make_tenant("unnamed-task-noset")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = (
            await c.post("/api/v1/tasks", json={"title": "Echte taak"}, headers=headers)
        ).json()
        patched = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"unnamed": True}, headers=headers
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["unnamed"] is False


# --------------------------------------------------------------------------- projects


async def test_a_create_then_edit_project_is_marked_and_findable(client_for) -> None:
    t = await make_tenant("unnamed-project")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        base = {"company_id": company, "status": "active", "budget_period": "total"}
        await c.post("/api/v1/projects", json={**base, "name": "Website"}, headers=headers)
        placeholder = (
            await c.post(
                "/api/v1/projects",
                json={**base, "name": "Naamloos project", "unnamed": True},
                headers=headers,
            )
        ).json()
        assert placeholder["unnamed"] is True

        only = (await c.get("/api/v1/projects?unnamed=true", headers=headers)).json()
        assert [item["id"] for item in only["items"]] == [placeholder["id"]]

        renamed = await c.patch(
            f"/api/v1/projects/{placeholder['id']}", json={"name": "Webshop"}, headers=headers
        )
        assert renamed.json()["unnamed"] is False
        assert (await c.get("/api/v1/projects?unnamed=true", headers=headers)).json()["total"] == 0


async def test_the_unnamed_filter_never_crosses_a_tenant(client_for) -> None:
    a = await make_tenant("unnamed-iso-a")
    b = await make_tenant("unnamed-iso-b")
    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        await c.post(
            "/api/v1/tasks", json={"title": "Naamloze taak", "unnamed": True}, headers=headers
        )
    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        assert (await c.get("/api/v1/tasks?unnamed=true", headers=headers)).json()["items"] == []
