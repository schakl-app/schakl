"""The project half of the Timeon sync (``docs/TIMEON.md`` §5a).

It existed as a *pairing* and nothing else: the run walked Timeon's project list, so a project
made in schakl was never seen, ``projects_direction = push`` wrote nothing, and an hour booked on
such a project went over with no project attached. Each test here is one thing that was either
missing or wrong, and the fake keeps budgets as a resource of their own and saves projects
wholesale, because those are the two facts about the live API a lazier fake would hide.
"""

from __future__ import annotations

import pytest

from app.integrations.timeon import client as timeon_client
from app.integrations.timeon.models import TimeonLinkKind, TimeonLinkOrigin
from tests.test_timeon_sync import (
    API_KEY,
    DAY,
    TIMEON_CUSTOMER,
    TIMEON_PROJECT,
    TIMEON_USER,
    _company,
    _connect,
    _links,
    _seed_remote,
    _sync,
    _tenant,
)
from tests.timeon_fake import FakeTimeon


@pytest.fixture
def timeon() -> FakeTimeon:
    fake = FakeTimeon()
    fake.api_key = API_KEY
    timeon_client.set_transport(fake.transport())
    yield fake
    timeon_client.set_transport(None)


PUSH = {"projects_direction": "push", "create_missing_projects": True}
TWO_WAY = {"projects_direction": "two_way", "create_missing_projects": True}


async def _project(client, headers, company_id: str, name: str = "Nieuwe site", **fields) -> dict:
    resp = await client.post(
        "/api/v1/projects",
        json={"company_id": company_id, "name": name, **fields},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_project(client, headers, project_id: str, **fields) -> None:
    resp = await client.patch(f"/api/v1/projects/{project_id}", json=fields, headers=headers)
    assert resp.status_code == 200, resp.text


async def _local_projects(client, headers) -> list[dict]:
    body = (await client.get("/api/v1/projects", headers=headers)).json()
    return body["items"] if isinstance(body, dict) else body


def _remote(timeon, name: str) -> dict:
    return next(p for p in timeon.projects if p["name"] == name)


# --------------------------------------------------------------------------------------- #
# Creating what Timeon has never seen
# --------------------------------------------------------------------------------------- #
async def test_a_project_made_here_is_created_in_timeon_with_its_budget(client_for, timeon) -> None:
    """The reported fault. Budget in **seconds** under unit 1, the billable default carried, and
    schakl's own id on the row so it says where it came from."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    company_id = await _company(client, headers)
    project = await _project(
        client, headers, company_id, budget_hours=40, billable_default=False
    )
    account_id = await _connect(client, headers, timeon, **PUSH)

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"], run
    assert run["counts"]["projects_pushed_new"] == 1

    row = _remote(timeon, "Nieuwe site")
    assert row["customerID"] == TIMEON_CUSTOMER
    assert row["defaultBillable"] is False
    assert row["externalID"] == project["id"]
    assert row["projectNumber"], "Timeon's own dialog always sends the next number"
    budget = timeon.budget_of(row["projectID"])
    assert (budget["unitType"], budget["value"], budget["periodType"]) == (1, 144000, 0)

    link = next(
        item
        for item in await _links(tenant, TimeonLinkKind.PROJECT.value)
        if item.external_id == str(row["projectID"])
    )
    assert str(link.local_id) == project["id"]
    assert link.origin == TimeonLinkOrigin.SCHAKL.value

    again = await _sync(client, headers, account_id, kind="full")
    assert "projects_pushed_new" not in again["counts"]
    assert len([p for p in timeon.projects if p["name"] == "Nieuwe site"]) == 1


async def test_an_hour_on_a_new_project_goes_over_with_its_project(client_for, timeon) -> None:
    """The consequence that made it visible: the hours arrived in Timeon on no project at all."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    company_id = await _company(client, headers)
    project = await _project(client, headers, company_id)
    resp = await client.post(
        "/api/v1/time/entries",
        json={
            "started_at": f"{DAY.isoformat()}T09:00:00Z",
            "ended_at": f"{DAY.isoformat()}T10:00:00Z",
            "company_id": company_id,
            "project_id": project["id"],
            "description": "Ontwerp",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    account_id = await _connect(client, headers, timeon, hours_direction="push", **PUSH)

    run = await _sync(client, headers, account_id, kind="full")
    assert run["counts"]["pushed_new"] == 1

    hour = next(iter(timeon.hours.values()))
    assert hour["projectID"] == _remote(timeon, "Nieuwe site")["projectID"]


async def test_a_dry_run_counts_the_project_and_creates_nothing(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _project(client, headers, await _company(client, headers), budget_hours=8)
    account_id = await _connect(client, headers, timeon, **PUSH)

    run = await _sync(client, headers, account_id, kind="full", dry_run=True)
    assert run["counts"]["projects_pushed_new"] == 1
    assert [p["name"] for p in timeon.projects] == ["CRM"]
    assert not [c for c in timeon.calls if c[1] in ("/api/project/create", "/api/budget")]


async def test_an_hours_run_writes_nothing_about_projects(client_for, timeon) -> None:
    """``kind`` is a promise. "Alleen koppelen" and an hours run pair references and stop."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _project(client, headers, await _company(client, headers))
    account_id = await _connect(client, headers, timeon, **PUSH)

    for kind in ("hours", "adopt"):
        await _sync(client, headers, account_id, kind=kind)
    assert [p["name"] for p in timeon.projects] == ["CRM"]


async def test_a_closed_or_clientless_project_is_not_pushed(client_for, timeon) -> None:
    """History nobody will book on again stays here, and a client Timeon has never heard of is
    *reported* — this integration pairs clients on their number and never invents one."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    company_id = await _company(client, headers)
    await _project(client, headers, company_id, name="Oud", status="archived")
    stranger = await _company(client, headers, name="Onbekend BV", number="999999")
    await _project(client, headers, stranger, name="Zonder klant daar")
    account_id = await _connect(client, headers, timeon, **PUSH)

    run = await _sync(client, headers, account_id, kind="full")
    assert [p["name"] for p in timeon.projects] == ["CRM"]
    assert [w["name"] for w in run["warnings"] if w["code"] == "project_no_customer"] == [
        "Zonder klant daar"
    ]


async def test_a_budget_that_resets_is_reported_and_not_sent(client_for, timeon) -> None:
    """Timeon's own screen cannot make a monthly budget, so there is nothing for one to become."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _project(
        client, headers, await _company(client, headers), budget_hours=10, budget_period="monthly"
    )
    account_id = await _connect(client, headers, timeon, **PUSH)

    run = await _sync(client, headers, account_id, kind="full")
    assert run["counts"]["projects_pushed_new"] == 1
    assert timeon.budget_of(_remote(timeon, "Nieuwe site")["projectID"]) is None
    assert "project_budget_period" in {w["code"] for w in run["warnings"]}

    again = await _sync(client, headers, account_id, kind="full")
    assert "projects_pushed" not in again["counts"], "unsayable is not a difference"


# --------------------------------------------------------------------------------------- #
# Keeping a paired project in step
# --------------------------------------------------------------------------------------- #
async def _paired(client_for, timeon, **policy) -> tuple:
    """CRM on both sides, agreeing (84 h, billable, open), and one run to put that on record."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    company_id = await _company(client, headers)
    project = await _project(client, headers, company_id, name="CRM", budget_hours=84)
    account_id = await _connect(client, headers, timeon, **policy)
    first = await _sync(client, headers, account_id, kind="full")
    assert first["counts"]["projects_in_step"] == 1, first
    return tenant, headers, client, account_id, project


async def test_a_budget_raised_here_reaches_timeon_and_keeps_its_switches(
    client_for, timeon
) -> None:
    """A budget is sent back **whole**: the switches schakl has no word for survive the edit."""
    timeon_budget_flags = {"useApproved": True, "visibility": 1}
    _tenant_, headers, client, account_id, project = await _paired(client_for, timeon, **PUSH)
    timeon.budgets[timeon.budget_of(TIMEON_PROJECT)["budgetID"]].update(timeon_budget_flags)
    budget_id = timeon.budget_of(TIMEON_PROJECT)["budgetID"]

    await _patch_project(client, headers, project["id"], budget_hours=100)
    run = await _sync(client, headers, account_id, kind="full")
    assert run["counts"]["projects_pushed"] == 1

    budget = timeon.budget_of(TIMEON_PROJECT)
    assert (budget["budgetID"], budget["value"]) == (budget_id, 360000)
    assert (budget["useApproved"], budget["visibility"]) == (True, 1)

    await _patch_project(client, headers, project["id"], budget_hours=None)
    await _sync(client, headers, account_id, kind="full")
    assert timeon.budget_of(TIMEON_PROJECT) is None, "a budget removed here is removed there"


async def test_renaming_here_renames_there_and_blanks_nothing(client_for, timeon) -> None:
    """``project/save`` replaces. A rename that sent only the name would cost the project its
    number and its remark, and nothing on either screen would say why."""
    _t, headers, client, account_id, project = await _paired(client_for, timeon, **PUSH)
    timeon.project(TIMEON_PROJECT).update({"projectNumber": "0042", "remark": "Let op: SLA"})

    await _patch_project(client, headers, project["id"], name="CRM 2.0", billable_default=False)
    await _sync(client, headers, account_id, kind="full")

    row = timeon.project(TIMEON_PROJECT)
    assert (row["name"], row["defaultBillable"]) == ("CRM 2.0", False)
    assert (row["projectNumber"], row["remark"], row["statusID"]) == ("0042", "Let op: SLA", 1)


async def test_closing_here_closes_there_through_the_status_endpoint(client_for, timeon) -> None:
    _t, headers, client, account_id, project = await _paired(client_for, timeon, **PUSH)
    before = len(timeon.calls)

    await _patch_project(client, headers, project["id"], status="archived")
    await _sync(client, headers, account_id, kind="full")

    assert timeon.project(TIMEON_PROJECT)["statusID"] == 2
    written = [c[1] for c in timeon.calls[before:] if c[0] != "GET" and "/list" not in c[1]]
    assert "/api/project/save" not in written, "a status change must not re-send the project"


async def test_a_rename_in_timeon_is_a_rename_not_a_new_project(client_for, timeon) -> None:
    """Pairing matched on the name every run, so a rename over there read as an unknown project
    — and with ``create_missing_projects`` on, the run made a second copy of it here."""
    _t, headers, client, account_id, _project_ = await _paired(client_for, timeon, **TWO_WAY)

    timeon.project(TIMEON_PROJECT)["name"] = "CRM (onderhoud)"
    run = await _sync(client, headers, account_id, kind="full")

    assert run["counts"]["projects_pulled"] == 1
    assert [p["name"] for p in await _local_projects(client, headers)] == ["CRM (onderhoud)"]
    assert [p["name"] for p in timeon.projects] == ["CRM (onderhoud)"]


async def test_two_people_two_fields_both_land(client_for, timeon) -> None:
    """Decided per field: a budget raised here and a project closed there are two changes by two
    people, and settling them as one would undo one of them."""
    _t, headers, client, account_id, project = await _paired(client_for, timeon, **TWO_WAY)

    await _patch_project(client, headers, project["id"], budget_hours=120)
    timeon.project(TIMEON_PROJECT)["statusID"] = 2
    run = await _sync(client, headers, account_id, kind="full")
    assert run["warnings"] == []

    assert timeon.budget_of(TIMEON_PROJECT)["value"] == 432000
    local = (await _local_projects(client, headers))[0]
    assert (local["status"], float(local["budget_hours"])) == ("archived", 120.0)


async def test_a_difference_nobody_recorded_waits_for_somebody_to_name_a_side(
    client_for, timeon
) -> None:
    """Every pairing made before this existed has no record of the two sides agreeing, so which
    one moved is unknowable. Two-way and ``manual`` therefore *ask* — and ``prefer`` answers."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    project = await _project(
        client, headers, await _company(client, headers), name="CRM", budget_hours=40
    )
    account_id = await _connect(client, headers, timeon, **TWO_WAY)

    run = await _sync(client, headers, account_id, kind="full")
    warning = next(w for w in run["warnings"] if w["code"] == "project_differs_budget")
    assert (warning["schakl"], warning["timeon"]) == ("40:00", "84:00")
    assert timeon.budget_of(TIMEON_PROJECT)["value"] == 302400
    assert float((await _local_projects(client, headers))[0]["budget_hours"]) == 40.0

    settled = await _sync(client, headers, account_id, kind="projects", prefer="schakl")
    assert settled["warnings"] == []
    assert timeon.budget_of(TIMEON_PROJECT)["value"] == 144000

    quiet = await _sync(client, headers, account_id, kind="full")
    assert quiet["counts"]["projects_in_step"] == 1
    assert project["id"] == str((await _links(tenant, TimeonLinkKind.PROJECT.value))[0].local_id)


async def test_a_euro_budget_is_not_read_as_seconds(client_for, timeon) -> None:
    """Unit 2 is euros. Read as seconds, € 1500 arrived here as a budget of 0,42 hours."""
    tenant, headers, client = await _tenant(client_for)
    timeon.add_user(TIMEON_USER, "Stan", tenant.user.email)
    timeon.add_customer(TIMEON_CUSTOMER, "Klant BV", "402148")
    timeon.add_project(TIMEON_PROJECT, TIMEON_CUSTOMER, "Campagne", budget_euros=1500)
    await _company(client, headers)
    account_id = await _connect(
        client, headers, timeon, projects_direction="pull", create_missing_projects=True
    )

    await _sync(client, headers, account_id, kind="projects")
    local = (await _local_projects(client, headers))[0]
    assert local["budget_hours"] is None
    assert float(local["budget_amount"]) == pytest.approx(1500.0)


async def test_a_refused_create_is_one_line_and_is_never_sent_twice(client_for, timeon) -> None:
    """A create is not retried on a 502 — it may well have happened, and a second one is a second
    project. The run reports it, carries on, and the next run looks before it tries again."""
    import httpx

    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _project(client, headers, await _company(client, headers))
    account_id = await _connect(client, headers, timeon, **PUSH)
    timeon.failures.append(("/api/project/create", httpx.Response(502, text="bad gateway")))

    run = await _sync(client, headers, account_id, kind="full")
    assert [e["code"] for e in run["errors"]] == ["project_push_failed"]
    assert len([c for c in timeon.calls if c[1] == "/api/project/create"]) == 1

    await _sync(client, headers, account_id, kind="full")
    assert len([p for p in timeon.projects if p["name"] == "Nieuwe site"]) == 1
