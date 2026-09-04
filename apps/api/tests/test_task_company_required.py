"""A task is always a client's, and it is named before it exists.

Two rules, one release, and both close a door that used to be open on purpose. The placeholder
create (#350) wrote a row titled *Naamloze taak* the moment somebody pressed `Nieuwe taak`, so
an abandoned create was work on somebody's board; the flag that marked those rows and the filter
that gathered them were mitigations for a row nobody had asked for. And a task with no client
was legal everywhere — on the API, from an automation rule, from a spreadsheet — which put it on
no client's page, in no client's export and outside every company horizon (§15), the one place
the agency's own work cannot be.

The shape is #392's, one column over: the rule lives in the write path (``TaskService.create``
refuses, ``TaskUpdate`` refuses clearing), the column stays nullable for the rows an instance
already carries (expand/contract, docs/WORKFLOW.md), and the creators with nobody in front of
them are covered too — except that a client, unlike a deadline, has no honest default, so the
system surface refuses rather than inventing one, and the import names the row.
"""

from __future__ import annotations

import csv
import io
import uuid

from sqlalchemy import text

from app.core.jobs import system_context
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.main import app
from app.modules.tasks.system import create_task_system
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, default_company, make_tenant

REFUSAL = "errors.tasks_company_required"


async def _company(c, headers, name: str = "Klant") -> str:
    res = await c.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _project(c, headers, company_id: str | None) -> str:
    body: dict = {"name": "Website"}
    if company_id is not None:
        body["company_id"] = company_id
    res = await c.post("/api/v1/projects", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #
async def test_a_task_cannot_be_created_without_a_client(client_for) -> None:
    t = await make_tenant("task-company-create")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        refused = await c.post(
            "/api/v1/tasks",
            json={"title": "Zonder klant", "due_date": FAR_FUTURE_DUE},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        # The envelope names the field *and* the sentence, so the dialog can put "kies een
        # klant" under the picker rather than "er ging iets mis" over the form (CLAUDE.md §9).
        assert refused.json()["error"]["fields"] == {"company_id": REFUSAL}

        explicit_null = await c.post(
            "/api/v1/tasks",
            json={"title": "Ook niet", "due_date": FAR_FUTURE_DUE, "company_id": None},
            headers=headers,
        )
        assert explicit_null.status_code == 422, explicit_null.text
        assert explicit_null.json()["error"]["fields"] == {"company_id": REFUSAL}

        # Nothing was written by either refusal.
        listing = (await c.get("/api/v1/tasks", headers=headers)).json()
        assert listing["items"] == []


async def test_naming_the_project_names_the_client(client_for) -> None:
    """A project has exactly one client, so a caller (an MCP agent, the project's own to-do
    list) may name the project alone and the task lands on that client."""
    t = await make_tenant("task-company-project")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        project = await _project(c, headers, company)
        created = await c.post(
            "/api/v1/tasks",
            json={"title": "Homepage", "due_date": FAR_FUTURE_DUE, "project_id": project},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["company_id"] == company
        assert created.json()["project_id"] == project


async def test_a_project_of_no_client_does_not_stand_in_for_one(client_for) -> None:
    """Never a silent widening: a project that names no client (a row from before projects
    required one) answers nothing, and the create is refused with the field named rather than
    filed under nobody."""
    t = await make_tenant("task-company-loose-project")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        project = await _project(c, headers, await _company(c, headers))
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(
                text("UPDATE projects SET company_id = NULL WHERE id = :id"),
                {"id": uuid.UUID(project)},
            )
            await session.commit()
        refused = await c.post(
            "/api/v1/tasks",
            json={"title": "Intern", "due_date": FAR_FUTURE_DUE, "project_id": project},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["fields"] == {"company_id": REFUSAL}


async def test_the_client_may_change_and_may_not_be_cleared(client_for) -> None:
    """CLAUDE.md §18's rule with its second half withdrawn (#392's shape): absent leaves the
    client alone, so a status-only PATCH keeps working on a row written before the rule."""
    t = await make_tenant("task-company-clear")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        alpha = await _company(c, headers, "Alpha")
        beta = await _company(c, headers, "Beta")
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Verhuizen", "due_date": FAR_FUTURE_DUE, "company_id": alpha},
                headers=headers,
            )
        ).json()

        moved = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"company_id": beta}, headers=headers
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["company_id"] == beta

        cleared = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"company_id": None}, headers=headers
        )
        assert cleared.status_code == 422, cleared.text
        assert cleared.json()["error"]["fields"] == {"company_id": REFUSAL}

        untouched = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"title": "Verhuisd"}, headers=headers
        )
        assert untouched.status_code == 200, untouched.text
        assert untouched.json()["company_id"] == beta


async def test_a_legacy_row_without_a_client_stays_editable(client_for) -> None:
    """The column is nullable for a release, so the rows an instance carries in must keep
    taking every other edit — the acceptance criterion that matters most on upgrade day."""
    t = await make_tenant("task-company-legacy")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "company_id": await default_company(c, headers),
                    "title": "Van vroeger",
                    "due_date": FAR_FUTURE_DUE,
                },
                headers=headers,
            )
        ).json()
        # The one shape no API can produce any more: a task with no client.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(
                text("UPDATE tasks SET company_id = NULL WHERE id = :id"),
                {"id": uuid.UUID(task["id"])},
            )
            await session.commit()

        opened = await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)
        assert opened.status_code == 200, opened.text
        assert opened.json()["company_id"] is None

        renamed = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"title": "Nog steeds"}, headers=headers
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["company_id"] is None


# --------------------------------------------------------------------------- #
# The name
# --------------------------------------------------------------------------- #
async def test_a_create_cannot_mark_a_task_unnamed(client_for) -> None:
    """The placeholder create is gone (#350): a caller that still sends the flag gets a named
    task — the title it chose is its title — and the list has no filter for the flag left."""
    t = await make_tenant("task-company-unnamed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/tasks",
            json={
                "company_id": await default_company(c, headers),
                "title": "Naamloze taak",
                "due_date": FAR_FUTURE_DUE,
                "unnamed": True,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["unnamed"] is False

    parameters = app.openapi()["paths"]["/api/v1/tasks"]["get"]["parameters"]
    assert "unnamed" not in {p["name"] for p in parameters}
    assert "unnamed" not in app.openapi()["components"]["schemas"]["TaskCreate"]["properties"]


async def test_a_whitespace_title_is_not_a_name(client_for) -> None:
    t = await make_tenant("task-company-blank-title")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await default_company(c, headers)
        blank = await c.post(
            "/api/v1/tasks",
            json={"title": "   ", "due_date": FAR_FUTURE_DUE, "company_id": company},
            headers=headers,
        )
        assert blank.status_code == 422, blank.text
        assert "title" in blank.json()["error"]["fields"]

        padded = await c.post(
            "/api/v1/tasks",
            json={
                "title": "  Offerte nabellen ",
                "due_date": FAR_FUTURE_DUE,
                "company_id": company,
            },
            headers=headers,
        )
        assert padded.status_code == 201, padded.text
        assert padded.json()["title"] == "Offerte nabellen"

        unnamed_again = await c.patch(
            f"/api/v1/tasks/{padded.json()['id']}", json={"title": " "}, headers=headers
        )
        assert unnamed_again.status_code == 422, unnamed_again.text


# --------------------------------------------------------------------------- #
# The creators with nobody in front of them
# --------------------------------------------------------------------------- #
async def test_the_system_surface_refuses_a_client_less_task_and_reads_the_project(
    client_for,
) -> None:
    """An automation rule has no one to ask, and — unlike the deadline — no honest default to
    give: the refusal surfaces on the run rather than as a task filed under nobody, and a rule
    that names a project gets that project's client."""
    t = await make_tenant("task-company-system")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        project = await _project(c, headers, company)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = system_context(t.org, session)
        try:
            await create_task_system(ctx, title="Zonder klant")
        except AppError as exc:
            assert exc.status_code == 422
            assert exc.fields == {"company_id": REFUSAL}
        else:
            raise AssertionError("a client-less system create was written")
        created = await create_task_system(
            ctx, title="Op het project", project_id=uuid.UUID(project)
        )
        assert str(created.company_id) == company
        await session.rollback()


async def test_an_import_names_the_row_that_has_no_client(client_for) -> None:
    """The import has a human reading a preview, so the column is required there and a missing
    one is reported by name rather than as a request-level 422 (CLAUDE.md §17, #289)."""
    t = await make_tenant("task-company-import")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        source = _csv([["title", "due_date"], ["Homepage", FAR_FUTURE_DUE]])
        report = (
            await c.post(
                "/api/v1/impex/task/inspect",
                files={"file": ("taken.csv", source, "text/csv")},
                headers=headers,
            )
        ).json()
        assert "company" in report["missing_required"]

        columns = (await c.get("/api/v1/impex/task/columns", headers=headers)).json()
        by_key = {col["key"]: col for col in columns["columns"]}
        assert by_key["company"]["required"] is True
