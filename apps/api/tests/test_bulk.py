"""Bulk edit / delete of a selection (``app/core/bulk/``), the core capability.

The engine is small and every one of its rules exists because the obvious alternative silently
does the wrong thing to rows nobody looked at. So the suite is organised around those rules
rather than around the entities:

* a shared value reaches **every** row, and a field the payload does not name reaches none;
* an explicit ``null`` clears only where the column says clearing is a real state;
* a value the *caller* got wrong refuses the whole call and writes nothing, while a refusal
  that belongs to **one row** is reported beside the rows that worked;
* the selection is the tenant's and the horizon's, however the ids were obtained;
* the read is one query however long the selection is.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.config import settings
from app.core.bulk import BulkDescriptor, BulkField
from app.core.bulk.spec import check_descriptor
from app.core.permissions.deps import iter_route_leaves
from app.db import async_session_maker, set_current_org
from app.main import app
from app.modules.invoicing.models import Invoice
from app.registry import registry
from tests.conftest import (
    FAR_FUTURE_DUE,
    add_membership,
    auth_cookie,
    default_company,
    make_tenant,
    org_today,
)
from tests.test_interactions_api import _seed_gmail_row
from tests.test_invoicing_api import _setup_org as _seed_invoicing_settings

#: The entities that opted in when the capability shipped — the floor the route sweep checks
#: against, so it can never pass by iterating a list a refactor emptied.
_SHIPPED_ENTITIES = {"company", "contact", "project", "subscription", "domain", "website", "task"}


async def _drop_permissions(org_id, keys: list[str]) -> None:
    """Take named permissions off every role in the org — a caller who simply lacks them."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        await session.execute(
            text("DELETE FROM role_permissions WHERE permission = ANY(:keys)"),
            {"keys": keys},
        )
        await session.commit()


async def _company(c, headers, name: str, **extra) -> dict:
    r = await c.post("/api/v1/companies", json={"name": name, **extra}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _domain(c, headers, name: str, company_id: str, **extra) -> dict:
    r = await c.post(
        "/api/v1/domains",
        json={"name": name, "company_id": company_id, **extra},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _task(c, headers, title: str, **extra) -> dict:
    r = await c.post(
        "/api/v1/tasks",
        json={
            "company_id": await default_company(c, headers),
            "due_date": FAR_FUTURE_DUE, "title": title, **extra,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# The shared value reaches every row — and only the fields it names
# --------------------------------------------------------------------------- #
async def test_one_call_writes_the_shared_value_to_every_selected_row(client_for) -> None:
    """The central promise: archiving twelve clients is one gesture, and it archives twelve.

    A batch that reported success while writing a prefix of its selection is the failure this
    pins — the caller has no way to notice, because the answer says three.
    """
    t = await make_tenant("bulk-write")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        ids = [(await _company(c, headers, name))["id"] for name in ("Acme", "Globex", "Initech")]

        result = await c.post(
            "/api/v1/bulk/company/update",
            json={"ids": ids, "values": {"status": "archived"}},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {"succeeded": 3, "failed": []}

        for company_id in ids:
            row = (await c.get(f"/api/v1/companies/{company_id}", headers=headers)).json()
            assert row["status"] == "archived"


async def test_a_field_the_payload_does_not_name_is_left_alone(client_for) -> None:
    """Absent means leave alone.

    The dialog opens blank over rows that disagree with each other, so reading "I did not fill
    this in" as "empty it everywhere" would wipe, on every row the user never looked at,
    exactly the field they had not thought about.
    """
    t = await make_tenant("bulk-absent")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(
            c, headers, "Acme", website="https://acme.test", city="Utrecht", status="lead"
        )

        result = await c.post(
            "/api/v1/bulk/company/update",
            json={"ids": [company["id"]], "values": {"status": "archived"}},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json()["succeeded"] == 1

        row = (await c.get(f"/api/v1/companies/{company['id']}", headers=headers)).json()
        assert row["status"] == "archived"
        assert row["website"] == "https://acme.test"
        assert row["city"] == "Utrecht"


# --------------------------------------------------------------------------- #
# Clearing: only where "empty" is a real answer
# --------------------------------------------------------------------------- #
async def test_an_explicit_null_clears_a_field_whose_empty_state_is_real(client_for) -> None:
    """``invoiceable`` is three-state (#298), so clearing it means "follow the register".

    That is the one case in this vocabulary where empty is an *answer* rather than an absence,
    and a bulk edit has to be able to say it — otherwise a decision made by a misfired batch
    can never be taken back to the register's own.
    """
    t = await make_tenant("bulk-clear")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme")
        domain = await _domain(c, headers, "example.nl", company["id"], invoiceable=True)
        assert domain["invoiceable"] is True

        result = await c.post(
            "/api/v1/bulk/domain/update",
            json={"ids": [domain["id"]], "values": {"invoiceable": None}},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {"succeeded": 1, "failed": []}

        row = (await c.get(f"/api/v1/domains/{domain['id']}", headers=headers)).json()
        assert row["invoiceable"] is None


async def test_clearing_a_field_that_has_no_empty_state_is_refused(client_for) -> None:
    """A domain with no client is nonsense, and a contact's link is set here but never unset.

    Both refuse for the same reason and with the same key, from two different sources: the
    domain's column is ``required`` and the contact's bulk descriptor overrides the import's
    ``clearable``. It is a 422 rather than a per-row failure because it is the payload that is
    wrong, and every row would fail on it identically.
    """
    t = await make_tenant("bulk-noclear")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme")
        domain = await _domain(c, headers, "example.nl", company["id"])
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "Jan", "company_ids": [company["id"]]},
                headers=headers,
            )
        ).json()

        refused = await c.post(
            "/api/v1/bulk/domain/update",
            json={"ids": [domain["id"]], "values": {"company": None}},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["fields"] == {"company": "errors.required"}
        # Not merely refused — untouched.
        assert (await c.get(f"/api/v1/domains/{domain['id']}", headers=headers)).json()[
            "company_id"
        ] == company["id"]

        # The same answer for the contact link, which the *bulk* descriptor makes unclearable
        # even though the import clears it with an empty cell.
        refused = await c.post(
            "/api/v1/bulk/contact/update",
            json={"ids": [contact["id"]], "values": {"company": None}},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["fields"] == {"company": "errors.required"}
        row = (await c.get(f"/api/v1/contacts/{contact['id']}", headers=headers)).json()
        assert [link["company_id"] for link in row["companies"]] == [company["id"]]


# --------------------------------------------------------------------------- #
# A bad value is the caller's, not a row's
# --------------------------------------------------------------------------- #
async def test_a_value_the_caller_got_wrong_refuses_the_call_and_writes_nothing(
    client_for,
) -> None:
    """Three ways to get the payload wrong, one answer: 422, named field, nothing written.

    Answering "0 succeeded, 40 failed" would bury the single thing the caller has to fix under
    forty identical copies of it — and an unknown key answered with a cheerful "succeeded: 40"
    would report success for a call that changed nothing at all.
    """
    t = await make_tenant("bulk-badvalue")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme", status="lead")
        contact = (
            await c.post("/api/v1/contacts", json={"first_name": "Jan"}, headers=headers)
        ).json()

        # A status that is not one of this entity's options.
        refused = await c.post(
            "/api/v1/bulk/company/update",
            json={"ids": [company["id"]], "values": {"status": "gearchiveerd"}},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["fields"] == {"status": "impex.errors.invalid_option"}

        # A client nothing in this tenant resolves to.
        refused = await c.post(
            "/api/v1/bulk/contact/update",
            json={"ids": [contact["id"]], "values": {"company": "Bestaat Niet BV"}},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["fields"] == {"company": "impex.errors.unresolved_reference"}

        # A key this entity has no column for — a typo in a dialog, not a field to ignore.
        refused = await c.post(
            "/api/v1/bulk/company/update",
            json={"ids": [company["id"]], "values": {"staus": "archived"}},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["fields"] == {"staus": "impex.errors.unknown_column"}

        # Nothing moved on the way to any of those refusals.
        assert (await c.get(f"/api/v1/companies/{company['id']}", headers=headers)).json()[
            "status"
        ] == "lead"
        assert (await c.get(f"/api/v1/contacts/{contact['id']}", headers=headers)).json()[
            "companies"
        ] == []


# --------------------------------------------------------------------------- #
# Rows are independent — the per-row savepoint
# --------------------------------------------------------------------------- #
async def test_one_rows_refusal_does_not_take_the_batch_down_with_it(client_for) -> None:
    """The property the whole engine rests on (``BulkService._attempt``).

    ``require_context`` rolls the request back on **any** exception, so a service refusal that
    escaped the batch would silently undo every row that had already worked. Each row therefore
    runs in its own SAVEPOINT: the refusal is reported, the rows beside it commit.

    The refusal here is the genuinely per-row kind — ``tasks.task.write`` is scoped, so a member
    holding ``:own`` moves their own task and is refused on a colleague's, in the middle of one
    batch (CLAUDE.md §15's two-layer rule: the route passes, the service refines).

    The refused row is sent **first**, because that is the direction the savepoint exists for:
    what must survive is the rows that come *after* the failure.

    Its ``errors.forbidden`` is also the other half of ``BulkService._reason``: a refusal that
    carries no ``fields`` is already its own reason and must pass through untouched, where the
    validation refusal in the next test has to be dug out from under the envelope's key.
    """
    t = await make_tenant("bulk-savepoint")
    owner_headers = await auth_cookie(t.user)
    member = await make_tenant("bulk-savepoint-m", email="member@bulk-savepoint.example")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.user.id, role="member")
        await session.commit()
    member_headers = await auth_cookie(member.user, org_id=t.org.id)

    async with client_for(t.host) as c:
        theirs = await _task(c, owner_headers, "Van de baas", assignee_user_id=str(t.user.id))
        mine = [
            await _task(c, owner_headers, title, assignee_user_id=str(member.user.id))
            for title in ("Van mij", "Ook van mij")
        ]

        result = await c.post(
            "/api/v1/bulk/task/update",
            json={
                "ids": [theirs["id"], *(task["id"] for task in mine)],
                "values": {"priority": "high"},
            },
            headers=member_headers,
        )
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["succeeded"] == 2
        assert payload["failed"] == [{"id": theirs["id"], "error": "errors.forbidden"}]

        # The rows after the refusal really committed — the session was still usable…
        for task in mine:
            assert (await c.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers)).json()[
                "priority"
            ] == "high"
        # …and the refused one is untouched, not merely unreported.
        assert (await c.get(f"/api/v1/tasks/{theirs['id']}", headers=owner_headers)).json()[
            "priority"
        ] == "normal"


async def test_a_row_the_service_validates_away_is_reported_beside_the_ones_that_worked(
    client_for,
) -> None:
    """The same contract for a *validation* refusal, which is the common shape in triage.

    Pushing a deadline back needs a reason (accountability, logged in the activity feed) and a
    batch cannot invent one, so a task that already has an earlier due date comes back in
    ``failed`` while a task with no deadline at all takes the new date.

    The **key** is pinned as tightly as the row, because for this whole class of refusal the two
    are not equally easy to get right. A service that refuses a value raises the envelope's
    generic ``errors.validation`` and puts the real reason under a *field*
    (``errors.due_reason_required``), so a batch that reported ``message_key`` would tell forty
    selected rows apart perfectly and then say "3 rows failed: invalid" — which names nothing
    the user can act on, and is what most refusals a batch meets would have read as.
    ``BulkService._reason`` is what closes that gap; this is the test that keeps it closed.
    """
    t = await make_tenant("bulk-duedate")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        no_deadline = await _task(c, headers, "Nog geen datum")
        earlier = await _task(c, headers, "Al gepland", due_date=today.replace(day=1).isoformat())

        target = today.replace(day=28).isoformat()
        result = await c.post(
            "/api/v1/bulk/task/update",
            json={
                "ids": [earlier["id"], no_deadline["id"]],
                "values": {"due_date": target},
            },
            headers=headers,
        )
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["succeeded"] == 1
        assert payload["failed"] == [{"id": earlier["id"], "error": "errors.due_reason_required"}]

        assert (await c.get(f"/api/v1/tasks/{no_deadline['id']}", headers=headers)).json()[
            "due_date"
        ] == target
        assert (await c.get(f"/api/v1/tasks/{earlier['id']}", headers=headers)).json()[
            "due_date"
        ] == today.replace(day=1).isoformat()


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #
async def test_a_bulk_call_cannot_reach_another_tenants_rows(client_for) -> None:
    """Golden Rule 1, on a surface where the ids come from the caller rather than a filter.

    The selection rides ``scoped_select()``, so a foreign id is simply absent — the same answer
    a single get gives, and never a hint that the row exists somewhere.
    """
    a = await make_tenant("bulk-iso-a")
    b = await make_tenant("bulk-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        company_a = await _company(ca, a_headers, "Acme", status="lead")

    async with client_for(b.host) as cb:
        result = await cb.post(
            "/api/v1/bulk/company/update",
            json={"ids": [company_a["id"]], "values": {"status": "archived"}},
            headers=b_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {
            "succeeded": 0,
            "failed": [{"id": company_a["id"], "error": "errors.not_found"}],
        }

    async with client_for(a.host) as ca:
        assert (await ca.get(f"/api/v1/companies/{company_a['id']}", headers=a_headers)).json()[
            "status"
        ] == "lead"


async def test_the_company_horizon_narrows_a_selection_exactly_as_it_narrows_a_list(
    client_for,
) -> None:
    """The third authorization axis (#191/#285), on a caller-supplied id list.

    The horizon is true here by construction — the selection rides ``scoped_select()`` — and
    that is the whole point of loading it through the repository rather than by id: a restricted
    manager can no more edit a client outside their groups in a batch than they can open it, and
    both answer "not found" rather than admitting the row exists.
    """
    t = await make_tenant("bulk-horizon")
    manager = await make_tenant("bulk-horizon-m", email="manager@bulk-horizon.example")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, manager.user.id, role="admin")
        membership_id = membership.id
        await session.commit()
    owner_headers = await auth_cookie(t.user)
    manager_headers = await auth_cookie(manager.user, org_id=t.org.id)

    async with client_for(t.host) as c:
        inside = await _company(c, owner_headers, "Alpha", status="lead")
        outside = await _company(c, owner_headers, "Beta", status="lead")
        group = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Team Noord"}, headers=owner_headers
            )
        ).json()
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/companies",
                json={"company_ids": [inside["id"]]},
                headers=owner_headers,
            )
        ).status_code == 204
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership_id)]},
                headers=owner_headers,
            )
        ).status_code == 204

        # The control: this caller may write companies, and one of the two ids is theirs.
        result = await c.post(
            "/api/v1/bulk/company/update",
            json={"ids": [inside["id"], outside["id"]], "values": {"status": "archived"}},
            headers=manager_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {
            "succeeded": 1,
            "failed": [{"id": outside["id"], "error": "errors.not_found"}],
        }

        assert (await c.get(f"/api/v1/companies/{inside['id']}", headers=owner_headers)).json()[
            "status"
        ] == "archived"
        assert (await c.get(f"/api/v1/companies/{outside['id']}", headers=owner_headers)).json()[
            "status"
        ] == "lead"


# --------------------------------------------------------------------------- #
# Permissions (CLAUDE.md §15)
# --------------------------------------------------------------------------- #
async def test_bulk_write_and_delete_each_need_the_entitys_own_permission(client_for) -> None:
    """A bulk route is the entity's own write, repeated — so it declares the entity's own key.

    Both halves are checked against a caller who demonstrably holds the **read**: without that
    control the 403 could just as well be "this member cannot see companies at all", and the
    test would pass while gating nothing.
    """
    t = await make_tenant("bulk-rbac")
    admin = await make_tenant("bulk-rbac-a", email="admin@bulk-rbac.example")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, admin.user.id, role="admin")
        await session.commit()
    owner_headers = await auth_cookie(t.user)
    admin_headers = await auth_cookie(admin.user, org_id=t.org.id)

    async with client_for(t.host) as c:
        company = await _company(c, owner_headers, "Acme", status="lead")
        ids = {"ids": [company["id"]]}

        # The control: this caller reads the entity, and holds both writes to begin with.
        assert (await c.get("/api/v1/companies", headers=admin_headers)).status_code == 200
        assert (
            await c.post(
                "/api/v1/bulk/company/update",
                json={**ids, "values": {"status": "onboarding"}},
                headers=admin_headers,
            )
        ).status_code == 200

    await _drop_permissions(t.org.id, ["companies.company.write"])
    async with client_for(t.host) as c:
        # Still reads — so the refusal below is about the write, not about the entity.
        assert (await c.get("/api/v1/companies", headers=admin_headers)).status_code == 200
        refused = await c.post(
            "/api/v1/bulk/company/update",
            json={**ids, "values": {"status": "archived"}},
            headers=admin_headers,
        )
        assert refused.status_code == 403, refused.text
        assert (await c.get(f"/api/v1/companies/{company['id']}", headers=owner_headers)).json()[
            "status"
        ] == "onboarding"

        # Delete is its own key, and holding it is what the delete route declares.
        assert (
            await c.post("/api/v1/bulk/company/delete", json=ids, headers=admin_headers)
        ).status_code == 200

    async with client_for(t.host) as c:
        company_two = await _company(c, owner_headers, "Globex")
    await _drop_permissions(t.org.id, ["companies.company.delete"])
    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/companies", headers=admin_headers)).status_code == 200
        refused = await c.post(
            "/api/v1/bulk/company/delete",
            json={"ids": [company_two["id"]]},
            headers=admin_headers,
        )
        assert refused.status_code == 403, refused.text
        assert (
            await c.get(f"/api/v1/companies/{company_two['id']}", headers=owner_headers)
        ).status_code == 200


# --------------------------------------------------------------------------- #
# The selection itself
# --------------------------------------------------------------------------- #
async def test_a_row_selected_twice_is_written_once(client_for) -> None:
    """A doubled post or a re-render must not make the answer count the same row twice."""
    t = await make_tenant("bulk-dupes")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme")

        result = await c.post(
            "/api/v1/bulk/company/update",
            json={
                "ids": [company["id"], company["id"], company["id"]],
                "values": {"status": "archived"},
            },
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {"succeeded": 1, "failed": []}


async def test_the_selection_and_the_payload_are_both_bounded(client_for) -> None:
    """Every shape a client can send that means nothing, refused before any work is done.

    ``MAX_BULK_IDS`` is the largest selection the pager can hand over — selection is per page
    and the pager tops out at 200. An empty selection and an empty ``values`` are calls that
    would otherwise report success having touched nothing at all.
    """
    t = await make_tenant("bulk-cap")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        too_many = [str(uuid.uuid4()) for _ in range(201)]
        assert (
            await c.post(
                "/api/v1/bulk/company/update",
                json={"ids": too_many, "values": {"status": "archived"}},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await c.post(
                "/api/v1/bulk/company/update",
                json={"ids": [], "values": {"status": "archived"}},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await c.post(
                "/api/v1/bulk/company/update",
                json={"ids": [str(uuid.uuid4())], "values": {}},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await c.post("/api/v1/bulk/company/delete", json={"ids": too_many}, headers=headers)
        ).status_code == 422


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #
async def test_bulk_delete_removes_the_selection_and_reports_what_was_already_gone(
    client_for,
) -> None:
    """Permanent, and per row: an id somebody deleted in another tab is one reported row.

    Refusing the whole batch over it would mean a stale tab can block a delete indefinitely,
    and "already gone" is exactly the outcome the caller asked for anyway.
    """
    t = await make_tenant("bulk-delete")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        kept = await _company(c, headers, "Blijft")
        doomed = [(await _company(c, headers, name))["id"] for name in ("Weg A", "Weg B")]
        gone = str(uuid.uuid4())

        result = await c.post(
            "/api/v1/bulk/company/delete",
            json={"ids": [*doomed, gone]},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {
            "succeeded": 2,
            "failed": [{"id": gone, "error": "errors.not_found"}],
        }

        for company_id in doomed:
            assert (
                await c.get(f"/api/v1/companies/{company_id}", headers=headers)
            ).status_code == 404
        assert (await c.get(f"/api/v1/companies/{kept['id']}", headers=headers)).status_code == 200


# --------------------------------------------------------------------------- #
# Query budget (docs/PERFORMANCE.md)
# --------------------------------------------------------------------------- #
async def test_the_whole_selection_is_loaded_in_one_query(client_for, count_queries) -> None:
    """The shape a functional test cannot see.

    Writing n rows is n writes and the total can never be constant, so pinning it would only
    pin a number nobody can keep. What must not scale with the selection is the **read**: one
    ``IN`` over the ids, never a ``get_or_404`` per id — which is where a bulk endpoint quietly
    becomes n round-trips before it has done any work at all.
    """
    t = await make_tenant("bulk-budget")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        ids = [(await _company(c, headers, f"Klant {i}"))["id"] for i in range(10)]

        with count_queries() as counter:
            result = await c.post(
                "/api/v1/bulk/company/update",
                json={"ids": ids, "values": {"status": "archived"}},
                headers=headers,
            )
        assert result.json()["succeeded"] == 10
        loads = [
            statement
            for statement in counter.matching("from companies")
            if "IN (" in statement.upper() and "companies.id" in statement
        ]
        assert len(loads) == 1, "\n\n".join(loads)


# --------------------------------------------------------------------------- #
# Anti-vacuum: the routes exist for everything that declared itself
# --------------------------------------------------------------------------- #
def test_every_registered_descriptor_gets_the_routes_it_declared() -> None:
    """A descriptor nothing mounts is a module that thinks it opted in and did not.

    Walked with ``iter_route_leaves`` because ``app.routes`` holds ``_IncludedRouter`` stubs,
    not routes — a path scan there finds nothing and stays green forever.
    """
    descriptors = [
        descriptor
        for module in registry.enabled(settings.enabled_modules)
        for descriptor in module.bulk
    ]
    assert descriptors, "no bulk descriptor is registered at all"
    names = {route.name for route in iter_route_leaves(app.routes)}

    for descriptor in descriptors:
        if descriptor.editable:
            assert f"bulk_update_{descriptor.entity_type}" in names
        if descriptor.delete_permission is not None:
            assert f"bulk_delete_{descriptor.entity_type}" in names

    # And the entities the feature shipped for are actually among them, so the sweep above
    # cannot pass by iterating an empty-ish set that lost an entity to a refactor.
    assert {descriptor.entity_type for descriptor in descriptors} >= _SHIPPED_ENTITIES


# --------------------------------------------------------------------------- #
# Delete-only entities: a descriptor that borrows no import shape
#
# An invoice is a numbered document and a contact moment is the record of something that was
# said, so neither has a CSV surface to borrow a column vocabulary from — and neither has a
# field a selection could sensibly be given one value for. What they do have is the case a
# batch is most obviously wanted for: a run of drafts generated from the wrong period, forty
# mis-logged e-mails. These pin that the second shape is a real shape — the descriptor names
# its own entity, mounts a delete route and **no update route at all** — and that the per-row
# savepoint is what makes a mixed selection an honest answer rather than a refusal.
# --------------------------------------------------------------------------- #
async def _invoice(c, headers, company_id: str, *, issue: bool = False) -> dict:
    created = await c.post(
        "/api/v1/invoicing/invoices",
        json={
            "company_id": company_id,
            "lines": [{"description": "Werk", "quantity": "1", "unit_price": "100"}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    if not issue:
        return created.json()
    issued = await c.post(
        f"/api/v1/invoicing/invoices/{created.json()['id']}/issue", json={}, headers=headers
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _interaction(c, headers, subject: str) -> dict:
    created = await c.post(
        "/api/v1/interactions",
        json={
            "kind": "physical_meeting",
            "occurred_at": "2026-07-10T14:30:00+00:00",
            "subject": subject,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()


async def test_a_mixed_invoice_selection_deletes_the_drafts_and_reports_the_rest(
    client_for,
) -> None:
    """The headline for the delete-only shape: a mixed selection is answered, not refused.

    ``InvoiceService.delete`` allows drafts only — an issued invoice is a numbered legal
    document, cancelled rather than removed — and somebody clearing out a duplicated run has
    both kinds on screen. Refusing the whole batch over the issued ones would make the control
    useless exactly when it is reached for; deleting them would destroy the numbering.

    So both halves are pinned at once. The drafts really go, each issued row comes back under
    its own key (``errors.invoicing.not_draft``, the 409 the single-row endpoint raises), and
    the draft sent **after** an issued one still commits — which is the per-row SAVEPOINT, and
    the reason a refusal that escaped the batch would have rolled the whole request back.
    """
    t = await make_tenant("bulk-inv-del")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _seed_invoicing_settings(c, headers)
        company = await _company(c, headers, "Klant BV")
        drafts = [await _invoice(c, headers, company["id"]) for _ in range(2)]
        issued = [await _invoice(c, headers, company["id"], issue=True) for _ in range(2)]
        assert [row["status"] for row in issued] == ["open", "open"]

        # Interleaved on purpose: what must survive a refusal is the rows that come after it.
        result = await c.post(
            "/api/v1/bulk/invoice/delete",
            json={
                "ids": [drafts[0]["id"], issued[0]["id"], drafts[1]["id"], issued[1]["id"]],
            },
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {
            "succeeded": 2,
            "failed": [
                {"id": issued[0]["id"], "error": "errors.invoicing.not_draft"},
                {"id": issued[1]["id"], "error": "errors.invoicing.not_draft"},
            ],
        }

        for draft in drafts:
            assert (
                await c.get(f"/api/v1/invoicing/invoices/{draft['id']}", headers=headers)
            ).status_code == 404
        # Not merely unreported — the numbered documents are still there, and still issued.
        for invoice in issued:
            still = await c.get(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
            assert still.status_code == 200, still.text
            assert still.json()["status"] == "open"
            assert still.json()["number"] == invoice["number"]


async def test_a_selection_of_interactions_deletes_whole_conversations(client_for) -> None:
    """The same contract for the other delete-only entity, over what its rows actually are.

    A page of Interacties is a page of **folded conversations** (#272), so a batch of four ticked
    rows is not a batch of four records. This selection is a three-message thread, a pending row
    and two hand-logged notes: six contact moments behind four rows, and all six go.

    Both halves of the original bug are pinned here. Nothing is *skipped* — ``delete`` used to
    share ``_reviewless_only`` with edit, which reads ``source == gmail`` and not the status, so
    every one of these e-mails came back in ``failed`` and the whole page answered "0 deleted".
    And the count is **6, not 4** — deleting only each fold's representative left the thread on
    screen while reporting success, which is the same complaint one layer along.
    """
    t = await make_tenant("bulk-int-del")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        thread = [
            await _seed_gmail_row(
                t, t.user.id, message_id=f"m{i}", thread_id="one-thread", pending=False
            )
            for i in range(3)
        ]
        pending = await _seed_gmail_row(t, t.user.id, message_id="p1", thread_id="p-thread")
        manual = [await _interaction(c, headers, subject) for subject in ("Kick-off", "Bellen")]

        # Exactly what the screen hands over: the ids of the *rows*, folds and all.
        listed = (await c.get("/api/v1/interactions", headers=headers)).json()
        assert listed["total"] == 4, [row["subject"] for row in listed["items"]]
        result = await c.post(
            "/api/v1/bulk/interaction/delete",
            json={"ids": [row["id"] for row in listed["items"]]},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        # One row deleted is one row succeeded; the thread's other two rode along with theirs.
        assert result.json() == {"succeeded": 4, "failed": []}

        for row_id in (*thread, pending, *(row["id"] for row in manual)):
            assert (
                await c.get(f"/api/v1/interactions/{row_id}", headers=headers)
            ).status_code == 404
        assert (await c.get("/api/v1/interactions", headers=headers)).json()["total"] == 0


def test_a_delete_only_entity_mounts_no_update_route() -> None:
    """Delete-only as a fact about the app, not an intention stated in a docstring.

    ``BulkService.update`` answers 404 for a descriptor with no writer, but that is the
    defence-in-depth half; what must be true first is that no update route exists to declare a
    write permission these entities never gave one for. A route pair mounted unconditionally
    would put "set a field on forty invoices" on the surface — which is a way to move money or
    a document's status without going through the endpoint that owns the rule.

    Walked with ``iter_route_leaves``: ``app.routes`` holds ``_IncludedRouter`` stubs, so a path
    scan there finds nothing and stays green forever. And an editable entity's update route is
    asserted *present* in the same breath, so the absences above cannot pass because the naming
    convention moved.
    """
    names = {route.name for route in iter_route_leaves(app.routes)}

    assert "bulk_update_company" in names, "the naming convention this test asserts against"
    for entity in ("invoice", "interaction"):
        assert f"bulk_delete_{entity}" in names
        assert f"bulk_update_{entity}" not in names


async def test_bulk_invoice_delete_needs_the_invoices_own_delete_permission(client_for) -> None:
    """A delete-only route declares the entity's own key, exactly as the edit routes do.

    Checked against a caller who demonstrably still **reads** invoices: without that control
    the 403 could as well be "this member cannot see invoicing at all", and the test would pass
    while gating nothing.
    """
    t = await make_tenant("bulk-inv-rbac")
    admin = await make_tenant("bulk-inv-rbac-a", email="admin@bulk-inv-rbac.example")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, admin.user.id, role="admin")
        await session.commit()
    owner_headers = await auth_cookie(t.user)
    admin_headers = await auth_cookie(admin.user, org_id=t.org.id)

    async with client_for(t.host) as c:
        await _seed_invoicing_settings(c, owner_headers)
        company = await _company(c, owner_headers, "Klant BV")
        draft = await _invoice(c, owner_headers, company["id"])
        assert (await c.get("/api/v1/invoicing/invoices", headers=admin_headers)).status_code == 200

    await _drop_permissions(t.org.id, ["invoicing.invoice.delete"])
    async with client_for(t.host) as c:
        # Still reads — so the refusal below is about the delete, not about the module.
        assert (await c.get("/api/v1/invoicing/invoices", headers=admin_headers)).status_code == 200
        refused = await c.post(
            "/api/v1/bulk/invoice/delete", json={"ids": [draft["id"]]}, headers=admin_headers
        )
        assert refused.status_code == 403, refused.text
        assert (
            await c.get(f"/api/v1/invoicing/invoices/{draft['id']}", headers=owner_headers)
        ).status_code == 200


async def test_a_delete_only_bulk_call_cannot_reach_another_tenants_rows(client_for) -> None:
    """Golden Rule 1 again, on the shape that has no import descriptor behind it.

    The selection rides ``scoped_select()`` whether or not there is a column vocabulary, so a
    foreign id is simply absent — and "not found" is the only thing it may ever read as.
    """
    a = await make_tenant("bulk-inv-iso-a")
    b = await make_tenant("bulk-inv-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(b.host) as cb:
        await _seed_invoicing_settings(cb, b_headers)
        company_b = await _company(cb, b_headers, "Klant van B")
        draft_b = await _invoice(cb, b_headers, company_b["id"])

    async with client_for(a.host) as ca:
        result = await ca.post(
            "/api/v1/bulk/invoice/delete", json={"ids": [draft_b["id"]]}, headers=a_headers
        )
        assert result.status_code == 200, result.text
        assert result.json() == {
            "succeeded": 0,
            "failed": [{"id": draft_b["id"], "error": "errors.not_found"}],
        }

    async with client_for(b.host) as cb:
        assert (
            await cb.get(f"/api/v1/invoicing/invoices/{draft_b['id']}", headers=b_headers)
        ).status_code == 200


async def _never_called(ctx, row) -> None:  # pragma: no cover - the descriptor never runs
    raise AssertionError("check_descriptor refuses before anything can call this")


def test_check_descriptor_refuses_a_descriptor_that_cannot_work() -> None:
    """Import time, not request time — a module's mistake must stop the app coming up.

    The two rules the delete-only shape added are the ones a copy-paste gets wrong. A
    descriptor that names neither an entity nor an import has no path segment to mount on, and
    ``entity_type`` would only discover that mid-request; one that declares editable columns
    with no import shape is declaring a vocabulary nothing can read — its columns, resolvers
    and writer all come from the import descriptor it does not have. Either surfaces as one
    tenant's confusing 500 halfway through a batch if it is allowed to load.
    """
    with pytest.raises(RuntimeError, match="neither an entity nor an import"):
        check_descriptor(
            BulkDescriptor(
                model=Invoice,
                delete_permission="invoicing.invoice.delete",
                delete_row=_never_called,
            )
        )

    with pytest.raises(RuntimeError, match="borrows no import shape"):
        check_descriptor(
            BulkDescriptor(
                model=Invoice,
                entity="invoice",
                editable=(BulkField("status"),),
            )
        )
