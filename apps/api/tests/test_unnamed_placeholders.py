"""A project nobody named says so, and naming it stops it saying so (#350).

Create-then-edit (#230) creates the record first and lands the user on its detail page in edit
mode. When they never finish, the placeholder name survives — and a placeholder *is* an ordinary
name, so it reads as real work. Worse, the placeholder was written in the **creator's** locale,
so one org held both "Naamloos project" and "Untitled project", alphabetised into two clumps no
search could gather.

The flag is what a name could never be: a fact about the row rather than a string in it. These
pin the three things that makes possible — marking, clearing, and finding — for **projects**.
Tasks used to be the other half of this file and are not any more: a task is named before it
exists (``TaskCreate`` carries no ``unnamed``, ``tests/test_task_company_required.py``), so the
flag on a task is read-only history for the rows an instance already had.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _company(c, headers) -> str:
    return (await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)).json()[
        "id"
    ]


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
        company = await _company(c, headers)
        await c.post(
            "/api/v1/projects",
            json={
                "company_id": company,
                "status": "active",
                "budget_period": "total",
                "name": "Naamloos project",
                "unnamed": True,
            },
            headers=headers,
        )
    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        assert (await c.get("/api/v1/projects?unnamed=true", headers=headers)).json()["items"] == []
