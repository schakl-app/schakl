"""The trash can (``app/core/trash/``, docs/TRASH.md), proved on clients.

Organised around the rules rather than the routes, because every rule exists because the
obvious alternative loses somebody's records:

* a delete is a *trash*: the record and everything that belongs to it hide everywhere and a
  restore brings all of it back unchanged;
* a client with a **history** — an issued invoice, a domain, an agreement, a project, hours —
  cannot be trashed at all, and the refusal names what stands in the way with the numbers;
* nothing can be attached to a client in the trash, and a trashed client still holds its number;
* the purge is the only path to the cascade, re-checked against the same blockers, and the
  nightly sweep keeps what it cannot purge and says so;
* the trail records all three verbs, the last one before the row goes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.core.trash.jobs import trash_purge
from app.db import async_session_maker, set_current_org
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant
from tests.test_company_groups import _setup as _horizon_setup
from tests.test_invoicing_api import _setup_org as _seed_invoicing


async def _company(c, headers, name: str, **extra) -> dict:
    r = await c.post("/api/v1/companies", json={"name": name, **extra}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _task(c, headers, company_id: str, title: str) -> dict:
    r = await c.post(
        "/api/v1/tasks",
        json={"company_id": company_id, "due_date": FAR_FUTURE_DUE, "title": title},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _issued_invoice(c, headers, company_id: str) -> dict:
    created = await c.post(
        "/api/v1/invoicing/invoices",
        json={
            "company_id": company_id,
            "lines": [{"description": "Werk", "quantity": "1", "unit_price": "100"}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    issued = await c.post(
        f"/api/v1/invoicing/invoices/{created.json()['id']}/issue", json={}, headers=headers
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _backdate_trash(org_id, company_id: str, days: int) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        await session.execute(
            text("UPDATE companies SET deleted_at = :at WHERE id = :cid"),
            {"at": datetime.now(UTC) - timedelta(days=days), "cid": uuid.UUID(company_id)},
        )
        await session.commit()


async def _trail(c, headers, company_id: str) -> list[str]:
    r = await c.get(
        "/api/v1/activity",
        params={"entity_type": "company", "entity_id": company_id},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return [row["action"] for row in r.json()]


# --------------------------------------------------------------------------- #
# trash + restore
# --------------------------------------------------------------------------- #
async def test_delete_trashes_the_client_and_hides_what_belongs_to_it(client_for) -> None:
    t = await make_tenant("trash-basic")
    h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, h, "Vergissing BV")
        keeper = await _company(c, h, "Blijft")
        task = await _task(c, h, company["id"], "Opruimen")

        preview = await c.get(f"/api/v1/trash/company/{company['id']}/preview", headers=h)
        assert preview.status_code == 200, preview.text
        assert preview.json()["can_trash"] is True
        assert preview.json()["blocking"] == []
        assert {(d["key"], d["count"]) for d in preview.json()["taken_along"]} == {
            ("tasks.tasks", 1)
        }
        assert preview.json()["retention_days"] == 30

        assert (await c.delete(f"/api/v1/companies/{company['id']}", headers=h)).status_code == 204

        # Hidden everywhere the live surfaces look…
        assert (await c.get(f"/api/v1/companies/{company['id']}", headers=h)).status_code == 404
        listed = (await c.get("/api/v1/companies", headers=h)).json()
        assert {row["name"] for row in listed["items"]} == {"Blijft"}
        assert listed["total"] == 1
        found = (await c.get("/api/v1/companies", params={"q": "Vergissing"}, headers=h)).json()
        assert found["items"] == []
        # …and so is what belonged to it: the task is on no board while its client is gone.
        assert (await c.get(f"/api/v1/tasks/{task['id']}", headers=h)).status_code == 404
        tasks = (await c.get("/api/v1/tasks", headers=h)).json()
        assert all(row["id"] != task["id"] for row in tasks["items"])
        # …but still in the database, untouched.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            assert await session.scalar(
                text("SELECT count(*) FROM tasks WHERE id = :tid"), {"tid": uuid.UUID(task["id"])}
            ) == 1

        # The trash says who, when, what goes with it and when it will be purged.
        page = (await c.get("/api/v1/trash/company", headers=h)).json()
        assert page["total"] == 1 and page["retention_days"] == 30
        item = page["items"][0]
        assert item["entity_id"] == company["id"] and item["label"] == "Vergissing BV"
        assert item["deleted_by_user_id"] == str(t.user.id)
        assert item["deleted_by_name"]
        purge_at = datetime.fromisoformat(item["purge_at"])
        deleted_at = datetime.fromisoformat(item["deleted_at"])
        assert purge_at - deleted_at == timedelta(days=30)
        assert [(d["key"], d["count"]) for d in item["taken_along"]] == [("tasks.tasks", 1)]
        single = await c.get(f"/api/v1/trash/company/{company['id']}", headers=h)
        assert single.status_code == 200 and single.json()["label"] == "Vergissing BV"
        # A live record is not in the trash.
        assert (await c.get(f"/api/v1/trash/company/{keeper['id']}", headers=h)).status_code == 404

        # Restore: everything is back exactly as it was.
        assert (
            await c.post(f"/api/v1/trash/company/{company['id']}/restore", headers=h)
        ).status_code == 204
        back = await c.get(f"/api/v1/companies/{company['id']}", headers=h)
        assert back.status_code == 200 and back.json()["name"] == "Vergissing BV"
        assert (await c.get(f"/api/v1/tasks/{task['id']}", headers=h)).status_code == 200
        assert (await c.get("/api/v1/trash/company", headers=h)).json()["total"] == 0
        assert (await _trail(c, h, company["id"]))[:2] == ["restored", "trashed"]


async def test_the_same_delete_twice_is_a_404_the_second_time(client_for) -> None:
    t = await make_tenant("trash-twice")
    h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, h, "Eenmalig")
        assert (await c.delete(f"/api/v1/companies/{company['id']}", headers=h)).status_code == 204
        assert (await c.delete(f"/api/v1/companies/{company['id']}", headers=h)).status_code == 404
        # …and restoring a live one is one too.
        other = await _company(c, h, "Levend")
        assert (
            await c.post(f"/api/v1/trash/company/{other['id']}/restore", headers=h)
        ).status_code == 404


# --------------------------------------------------------------------------- #
# blockers: a client with a history is archived, never deleted
# --------------------------------------------------------------------------- #
async def test_a_client_with_an_issued_invoice_cannot_be_deleted(client_for) -> None:
    t = await make_tenant("trash-invoice")
    h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _seed_invoicing(c, h)
        company = await _company(c, h, "Boekhouding BV", invoice_email="f@klant.nl")
        invoice = await _issued_invoice(c, h, company["id"])

        preview = (await c.get(f"/api/v1/trash/company/{company['id']}/preview", headers=h)).json()
        assert preview["can_trash"] is False
        assert [(d["key"], d["count"]) for d in preview["blocking"]] == [
            ("invoicing.invoices_issued", 1)
        ]

        refused = await c.delete(f"/api/v1/companies/{company['id']}", headers=h)
        assert refused.status_code == 409, refused.text
        body = refused.json()["error"]
        assert body["message"] == "errors.trash_blocked"
        assert body["details"] == {"blocking": {"invoicing.invoices_issued": 1}}

        # Nothing moved: the client is live, the invoice is still there with its number.
        assert (await c.get(f"/api/v1/companies/{company['id']}", headers=h)).status_code == 200
        still = await c.get(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=h)
        assert still.status_code == 200 and still.json()["number"] == invoice["number"]
        # A draft is not a record: it goes along, it does not block.
        drafted = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company["id"],
                "lines": [{"description": "Later", "quantity": "1", "unit_price": "1"}],
            },
            headers=h,
        )
        assert drafted.status_code == 201
        preview = (await c.get(f"/api/v1/trash/company/{company['id']}/preview", headers=h)).json()
        assert {d["key"] for d in preview["taken_along"]} == {"invoicing.invoice_drafts"}


async def test_domains_hosting_subscriptions_projects_and_hours_block(client_for) -> None:
    t = await make_tenant("trash-blockers")
    h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, h, "Vol BV")
        assert (
            await c.post(
                "/api/v1/domains",
                json={"name": "vol.nl", "company_id": company["id"]},
                headers=h,
            )
        ).status_code == 201
        assert (
            await c.post(
                "/api/v1/hosting", json={"name": "Server 1", "company_id": company["id"]}, headers=h
            )
        ).status_code == 201
        assert (
            await c.post(
                "/api/v1/projects", json={"name": "Site", "company_id": company["id"]}, headers=h
            )
        ).status_code == 201
        hours = await c.post(
            "/api/v1/time/entries",
            json={
                "company_id": company["id"],
                "started_at": "2026-01-05T09:00:00+00:00",
                "minutes": 60,
            },
            headers=h,
        )
        assert hours.status_code == 201, hours.text

        refused = await c.delete(f"/api/v1/companies/{company['id']}", headers=h)
        assert refused.status_code == 409
        blocking = refused.json()["error"]["details"]["blocking"]
        assert blocking == {
            "domains.domains": 1,
            "hosting.accounts": 1,
            "projects.projects": 1,
            "time.entries": 1,
        }


# --------------------------------------------------------------------------- #
# what a trashed client cannot do
# --------------------------------------------------------------------------- #
async def test_nothing_can_be_attached_to_a_trashed_client(client_for) -> None:
    t = await make_tenant("trash-attach")
    h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, h, "Weg BV")
        assert (await c.delete(f"/api/v1/companies/{company['id']}", headers=h)).status_code == 204
        # The core parent check (tasks, projects, time) and the modules' own raw checks
        # (domains, hosting, contacts, subscriptions) both refuse — as a 404, the answer reading
        # the client gets.
        task = await c.post(
            "/api/v1/tasks",
            json={"company_id": company["id"], "due_date": FAR_FUTURE_DUE, "title": "Nee"},
            headers=h,
        )
        assert task.status_code == 404, task.text
        domain = await c.post(
            "/api/v1/domains", json={"name": "weg.nl", "company_id": company["id"]}, headers=h
        )
        assert domain.status_code == 404, domain.text
        contact = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Ada", "company_ids": [company["id"]]},
            headers=h,
        )
        assert contact.status_code == 404, contact.text
        # Editing it is refused too: to every live surface it does not exist.
        assert (
            await c.patch(f"/api/v1/companies/{company['id']}", json={"name": "X"}, headers=h)
        ).status_code == 404


async def test_a_trashed_client_still_holds_its_client_number(client_for) -> None:
    t = await make_tenant("trash-number")
    h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        first = await _company(c, h, "Eerste", client_number="1001")
        assert (await c.delete(f"/api/v1/companies/{first['id']}", headers=h)).status_code == 204
        # The partial unique index still holds the number; the service must say so rather than
        # let the insert hit it.
        taken = await c.post(
            "/api/v1/companies", json={"name": "Tweede", "client_number": "1001"}, headers=h
        )
        assert taken.status_code == 409, taken.text
        assert taken.json()["error"]["fields"] == {
            "client_number": "errors.companies.client_number_taken"
        }
        # Purging frees it.
        assert (
            await c.delete(f"/api/v1/trash/company/{first['id']}", headers=h)
        ).status_code == 204
        freed = await c.post(
            "/api/v1/companies", json={"name": "Tweede", "client_number": "1001"}, headers=h
        )
        assert freed.status_code == 201, freed.text


# --------------------------------------------------------------------------- #
# purge — by hand and by the sweep
# --------------------------------------------------------------------------- #
async def test_purge_is_the_cascade_behind_the_same_blockers_and_writes_the_trail(
    client_for,
) -> None:
    t = await make_tenant("trash-purge")
    h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, h, "Definitief BV")
        task = await _task(c, h, company["id"], "Gaat mee")
        # A live record cannot be purged through the trash: it is not in it.
        assert (
            await c.delete(f"/api/v1/trash/company/{company['id']}", headers=h)
        ).status_code == 404
        assert (await c.delete(f"/api/v1/companies/{company['id']}", headers=h)).status_code == 204

        # A blocker that appears *after* trashing keeps the record: move a domain onto it by
        # SQL, the one way a hidden client can still gain one.
        other = await _company(c, h, "Ander")
        domain = await c.post(
            "/api/v1/domains", json={"name": "blok.nl", "company_id": other["id"]}, headers=h
        )
        assert domain.status_code == 201
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(
                text("UPDATE domains SET company_id = :cid WHERE id = :did"),
                {"cid": uuid.UUID(company["id"]), "did": uuid.UUID(domain.json()["id"])},
            )
            await session.commit()
        kept = await c.delete(f"/api/v1/trash/company/{company['id']}", headers=h)
        assert kept.status_code == 409
        assert kept.json()["error"]["details"] == {"blocking": {"domains.domains": 1}}
        listed = (await c.get("/api/v1/trash/company", headers=h)).json()["items"][0]
        assert [(d["key"], d["count"]) for d in listed["blocking"]] == [("domains.domains", 1)]

        # Take the blocker away again and purge for real.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(
                text("UPDATE domains SET company_id = :cid WHERE id = :did"),
                {"cid": uuid.UUID(other["id"]), "did": uuid.UUID(domain.json()["id"])},
            )
            await session.commit()
        assert (
            await c.delete(f"/api/v1/trash/company/{company['id']}", headers=h)
        ).status_code == 204
        assert (await c.get(f"/api/v1/trash/company/{company['id']}", headers=h)).status_code == 404
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            assert await session.scalar(
                text("SELECT count(*) FROM companies WHERE id = :cid"),
                {"cid": uuid.UUID(company["id"])},
            ) == 0
            # The tasks module took its rows out itself (not the FK's SET NULL).
            assert await session.scalar(
                text("SELECT count(*) FROM tasks WHERE id = :tid"), {"tid": uuid.UUID(task["id"])}
            ) == 0
        # The trail outlives the record and names what disappeared.
        actions = await _trail(c, h, company["id"])
        assert actions[:2] == ["purged", "trashed"]


async def test_the_sweep_purges_past_retention_and_keeps_what_it_cannot(client_for) -> None:
    t = await make_tenant("trash-sweep")
    h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        old = await _company(c, h, "Oud")
        fresh = await _company(c, h, "Vers")
        blocked = await _company(c, h, "Geblokkeerd")
        for company in (old, fresh, blocked):
            assert (
                await c.delete(f"/api/v1/companies/{company['id']}", headers=h)
            ).status_code == 204
        await _backdate_trash(t.org.id, old["id"], days=31)
        await _backdate_trash(t.org.id, blocked["id"], days=40)
        # ``blocked`` grew a blocker while in the trash (SQL — the only way it can).
        anchor = await _company(c, h, "Anker")
        domain = await c.post(
            "/api/v1/domains", json={"name": "anker.nl", "company_id": anchor["id"]}, headers=h
        )
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(
                text("UPDATE domains SET company_id = :cid WHERE id = :did"),
                {"cid": uuid.UUID(blocked["id"]), "did": uuid.UUID(domain.json()["id"])},
            )
            await session.commit()

        summary = await trash_purge({})
        assert "purged=1" in summary and "kept=1" in summary

        remaining = (await c.get("/api/v1/trash/company", headers=h)).json()
        assert {row["label"] for row in remaining["items"]} == {"Vers", "Geblokkeerd"}
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            assert await session.scalar(
                text("SELECT count(*) FROM companies WHERE id = :cid"),
                {"cid": uuid.UUID(old["id"])},
            ) == 0
        # The sweep ran as the system: the purged line has no actor.
        activity = await c.get(
            "/api/v1/activity",
            params={"entity_type": "company", "entity_id": old["id"]},
            headers=h,
        )
        purged = next(row for row in activity.json() if row["action"] == "purged")
        assert purged["actor_name"] is None and purged["payload"]["label"] == "Oud"


# --------------------------------------------------------------------------- #
# bulk, permissions and the horizon
# --------------------------------------------------------------------------- #
async def test_bulk_delete_trashes_what_it_can_and_names_the_rest(client_for) -> None:
    t = await make_tenant("trash-bulk")
    h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        free = await _company(c, h, "Vrij")
        held = await _company(c, h, "Vast")
        assert (
            await c.post(
                "/api/v1/domains", json={"name": "vast.nl", "company_id": held["id"]}, headers=h
            )
        ).status_code == 201
        r = await c.post(
            "/api/v1/bulk/company/delete", json={"ids": [free["id"], held["id"]]}, headers=h
        )
        assert r.status_code == 200, r.text
        assert r.json()["succeeded"] == 1
        assert r.json()["failed"] == [{"id": held["id"], "error": "errors.trash_blocked"}]
        assert (await c.get(f"/api/v1/companies/{held['id']}", headers=h)).status_code == 200
        assert (await c.get("/api/v1/trash/company", headers=h)).json()["total"] == 1


async def test_the_trash_is_read_through_the_company_horizon(client_for) -> None:
    t, member, membership, owner_h, member_h, a, b, group = await _horizon_setup(
        client_for, "trash-horiz", role="admin"
    )
    async with client_for(t.host) as c:
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204
        for company in (a, b):
            assert (
                await c.delete(f"/api/v1/companies/{company['id']}", headers=owner_h)
            ).status_code == 204
        # The owner's trash holds both; the restricted admin's holds only their own client.
        assert (await c.get("/api/v1/trash/company", headers=owner_h)).json()["total"] == 2
        mine = (await c.get("/api/v1/trash/company", headers=member_h)).json()
        assert [row["label"] for row in mine["items"]] == ["Alpha"]
        assert (
            await c.get(f"/api/v1/trash/company/{b['id']}", headers=member_h)
        ).status_code == 404
        assert (
            await c.post(f"/api/v1/trash/company/{b['id']}/restore", headers=member_h)
        ).status_code == 404
        # A member without the delete permission has no trash at all.
        plain = await make_tenant("trash-horiz-plain", email="plain-trash@example.com")
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            from tests.conftest import add_membership

            await add_membership(session, t.org.id, plain.user.id, role="member")
            await session.commit()
        plain_h = await auth_cookie(plain.user, org_id=t.org.id)
        assert (await c.get("/api/v1/trash/company", headers=plain_h)).status_code == 403
