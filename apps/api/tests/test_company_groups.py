"""Company groups / the per-membership company data horizon (issue #191).

The third authorization axis: a restricted membership sees only the union of its groups'
companies — across every company-rooted module — while a membership with no assignments
(and every owner) keeps seeing everything.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant


async def _setup(client_for, slug: str, *, role: str = "member"):
    """An owner, a second membership, two companies, and one group holding only company A.

    ``role="admin"`` is the *restricted manager*: someone who may read invoices, quotes and
    subscriptions and write a website at all, and is still scoped to a portfolio. A plain member
    holds none of those keys, so a leak in them would hide behind a 403 rather than show up.
    """
    t = await make_tenant(slug)
    member = await make_tenant(f"{slug}-m", email=f"member-{slug}@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, member.user.id, role=role)
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


async def test_horizon_filters_the_interactions_overview(client_for) -> None:
    """The cross-client Interacties feed (#240).

    It folds conversations in a window subquery and totals DISTINCT conversations, so it never
    passed through ``scoped_select()`` and had no horizon at all: a membership scoped to one
    group read every client's contact moments org-wide.
    """
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz-int")

    async with client_for(t.host) as c:
        for company, subject in ((a, "Alpha kick-off"), (b, "Beta kick-off"), (None, "Intern")):
            body = {
                "kind": "physical_meeting",
                "occurred_at": "2026-07-10T14:30:00+00:00",
                "subject": subject,
            }
            if company:
                body["company_id"] = company["id"]
            created = await c.post("/api/v1/interactions", json=body, headers=owner_h)
            assert created.status_code == 201, created.text

        # Unassigned, the member still sees all three — the horizon only bites once assigned.
        before = (await c.get("/api/v1/interactions?limit=50", headers=member_h)).json()
        assert {r["subject"] for r in before["items"]} == {
            "Alpha kick-off",
            "Beta kick-off",
            "Intern",
        }

        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204

        # Alpha's row and the company-less one; never Beta's.
        listed = (await c.get("/api/v1/interactions?limit=50", headers=member_h)).json()
        assert {r["subject"] for r in listed["items"]} == {"Alpha kick-off", "Intern"}
        # The total counts what the page could return, not the org's rows (#252's rule).
        assert listed["total"] == 2

        # An explicit filter on an invisible company yields nothing — not that client's timeline.
        scoped = (
            await c.get(
                "/api/v1/interactions", params={"company_id": b["id"]}, headers=member_h
            )
        ).json()
        assert scoped["items"] == [] and scoped["total"] == 0

        # The owner is never restricted, and the company panel's own path is unchanged.
        owner_view = (await c.get("/api/v1/interactions?limit=50", headers=owner_h)).json()
        assert owner_view["total"] == 3
        panel = (
            await c.get("/api/v1/interactions", params={"company_id": b["id"]}, headers=owner_h)
        ).json()
        assert {r["subject"] for r in panel["items"]} == {"Beta kick-off"}


async def test_horizon_filters_an_interaction_thread(client_for) -> None:
    """Reaching one visible message must not open the rest of its conversation (#240).

    A conversation is glued by ``conversation_id`` and its messages can be filed under
    different clients, so the thread fetch — and the fold badge that promises its length —
    carry the horizon exactly like the feed does.
    """
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz-thr")

    async with client_for(t.host) as c:
        ids = {}
        for company, subject in ((a, "Alpha bericht"), (b, "Beta bericht")):
            created = await c.post(
                "/api/v1/interactions",
                json={
                    "kind": "physical_meeting",
                    "occurred_at": "2026-07-10T14:30:00+00:00",
                    "subject": subject,
                    "company_id": company["id"],
                },
                headers=owner_h,
            )
            assert created.status_code == 201, created.text
            ids[subject] = created.json()["id"]

        # Glue the two into one conversation. Manual rows never fold on their own (#272), so
        # the id is set directly — the state a remapped email thread reaches on its own.
        conversation_id = uuid.uuid4()
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(
                text("UPDATE interactions SET conversation_id = :c WHERE id = ANY(:ids)"),
                {"c": conversation_id, "ids": [uuid.UUID(i) for i in ids.values()]},
            )
            await session.commit()

        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204

        # The owner reads the whole thread; the scoped member only their group's message…
        assert (
            len(
                (
                    await c.get(
                        f"/api/v1/interactions/{ids['Alpha bericht']}/thread", headers=owner_h
                    )
                ).json()
            )
            == 2
        )
        thread = (
            await c.get(f"/api/v1/interactions/{ids['Alpha bericht']}/thread", headers=member_h)
        ).json()
        assert {r["subject"] for r in thread} == {"Alpha bericht"}

        # …and the badge counts what that thread will actually open, not the hidden rows.
        listed = (await c.get("/api/v1/interactions?limit=50", headers=member_h)).json()
        assert [r["conversation_count"] for r in listed["items"]] == [1]

        # Beta's own message stays unreachable by id, as 404 — existence must not leak.
        assert (
            await c.get(f"/api/v1/interactions/{ids['Beta bericht']}", headers=member_h)
        ).status_code == 404


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


# --------------------------------------------------------------------------- #
# Complete isolation (#285)
# --------------------------------------------------------------------------- #
async def _seed_for_both(c, owner_h, a, b) -> dict[str, dict]:
    """One recognisable row of every company-rooted entity, for each client."""
    made: dict[str, dict] = {}
    for key, company in (("a", a), ("b", b)):
        rows: dict[str, dict] = {}
        made[key] = rows

        def keep(name: str, response, *, into=rows, side=key) -> None:
            assert response.status_code == 201, f"{name}/{side}: {response.text}"
            into[name] = response.json()

        keep(
            "contact",
            await c.post(
                "/api/v1/contacts",
                json={"first_name": f"Person-{key}", "company_ids": [company["id"]]},
                headers=owner_h,
            ),
        )
        keep(
            "domain",
            await c.post(
                "/api/v1/domains",
                json={"name": f"klant-{key}.nl", "company_id": company["id"]},
                headers=owner_h,
            ),
        )
        keep(
            "website",
            await c.post(
                "/api/v1/websites",
                json={"domain_id": rows["domain"]["id"], "root": True},
                headers=owner_h,
            ),
        )
        keep(
            "hosting",
            await c.post(
                "/api/v1/hosting",
                json={"name": f"host-{key}", "company_id": company["id"]},
                headers=owner_h,
            ),
        )
        keep(
            "time",
            await c.post(
                "/api/v1/time/entries",
                json={
                    "started_at": "2026-07-10T09:00:00+00:00",
                    "minutes": 60,
                    "company_id": company["id"],
                    "description": f"Time-{key}",
                },
                headers=owner_h,
            ),
        )
        keep(
            "invoice",
            await c.post(
                "/api/v1/invoicing/invoices",
                json={"company_id": company["id"], "reference": f"Inv-{key}", "lines": []},
                headers=owner_h,
            ),
        )
        keep(
            "quote",
            await c.post(
                "/api/v1/invoicing/quotes",
                json={"company_id": company["id"], "reference": f"Quo-{key}", "lines": []},
                headers=owner_h,
            ),
        )
        keep(
            "subscription",
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company["id"],
                    "name": f"Sub-{key}",
                    "amount": "10.00",
                    "start_date": "2026-01-01",
                },
                headers=owner_h,
            ),
        )
    return made


async def test_horizon_reaches_entities_with_no_company_column(client_for) -> None:
    """A ``company_id`` column is not the only way a row belongs to a client (#285).

    ``contacts`` links through ``company_contacts`` and ``websites`` through its parent domain,
    so the repository's column-matched horizon found nothing to filter on and did **nothing**:
    a membership scoped to one company group read the agency's whole address book and every
    client's websites — list, total and get-by-id. Both models now declare the predicate
    themselves (``__company_horizon_clause__``), so every repository path carries it.
    """
    t, member, membership, owner_h, member_h, a, b, group = await _setup(
        client_for, "horiz-noco", role="admin"
    )

    async with client_for(t.host) as c:
        made = await _seed_for_both(c, owner_h, a, b)
        # A contact attached to nobody stays visible to staff — the same rule a company-less
        # task rides ("no company linkage" is not company data).
        loose = await c.post(
            "/api/v1/contacts", json={"first_name": "Zwevend"}, headers=owner_h
        )
        assert loose.status_code == 201, loose.text

        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204

        contacts = (await c.get("/api/v1/contacts?limit=50", headers=member_h)).json()
        assert {r["first_name"] for r in contacts["items"]} == {"Person-a", "Zwevend"}
        assert contacts["total"] == 2
        assert (
            await c.get(f"/api/v1/contacts/{made['b']['contact']['id']}", headers=member_h)
        ).status_code == 404
        # Filtering on the invisible client answers 404, not "that client has no people".
        assert (
            await c.get(
                "/api/v1/contacts", params={"company_id": b["id"]}, headers=member_h
            )
        ).status_code == 404

        websites = (await c.get("/api/v1/websites?limit=50", headers=member_h)).json()
        assert [r["domain_name"] for r in websites["items"]] == ["klant-a.nl"]
        assert websites["total"] == 1
        assert (
            await c.get(f"/api/v1/websites/{made['b']['website']['id']}", headers=member_h)
        ).status_code == 404
        # …nor may they *create* one on the invisible client's domain: a website carries no
        # company_id, so the repository's write guard has nothing to refuse.
        assert (
            await c.post(
                "/api/v1/websites",
                json={"domain_id": made["b"]["domain"]["id"], "root": False},
                headers=member_h,
            )
        ).status_code == 404

        # The owner is never restricted.
        assert (await c.get("/api/v1/contacts?limit=50", headers=owner_h)).json()["total"] == 3
        assert (await c.get("/api/v1/websites?limit=50", headers=owner_h)).json()["total"] == 2


async def test_horizon_reaches_totals_and_summary_tiles(client_for) -> None:
    """A count is a fact about rows the caller cannot see (#252's rule, still open here).

    Invoices, quotes, subscriptions and time entries each built ``total`` with a hand-rolled
    ``select(count())``, and the invoicing summary tiles are six conditional aggregates in one
    raw statement — so a restricted membership read a list of one under a header saying two.
    """
    t, member, membership, owner_h, member_h, a, b, group = await _setup(
        client_for, "horiz-count", role="admin"
    )

    async with client_for(t.host) as c:
        await _seed_for_both(c, owner_h, a, b)
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204

        for path, label in (
            ("/api/v1/invoicing/invoices", "invoices"),
            ("/api/v1/invoicing/quotes", "quotes"),
            ("/api/v1/subscriptions", "subscriptions"),
        ):
            body = (await c.get(f"{path}?limit=50", headers=member_h)).json()
            assert len(body["items"]) == 1, label
            assert body["total"] == 1, f"{label}: total counted the org, not the horizon"
            assert (await c.get(f"{path}?limit=50", headers=owner_h)).json()["total"] == 2, label

        # The manager report is where the time-entry count leaked: the default per-user filter
        # hid it, since the owner's rows are not the caller's own.
        hours = (
            await c.get("/api/v1/time/entries?all_users=true&limit=50", headers=member_h)
        ).json()
        assert [r["description"] for r in hours["items"]] == ["Time-a"]
        assert hours["total"] == 1
        owner_hours = (
            await c.get("/api/v1/time/entries?all_users=true&limit=50", headers=owner_h)
        ).json()
        assert owner_hours["total"] == 2

        # /time/report rides scoped_select for its rows but hand-builds its totals
        # aggregate — which silently skipped the horizon, so a scoped manager read
        # org-wide minutes above their own filtered rows.
        report = (await c.get("/api/v1/time/report", headers=member_h)).json()
        assert [r["description"] for r in report["items"]] == ["Time-a"]
        assert report["totals"]["count"] == 1
        assert report["totals"]["minutes"] == 60
        owner_report = (await c.get("/api/v1/time/report", headers=owner_h)).json()
        assert owner_report["totals"]["count"] == 2
        assert owner_report["totals"]["minutes"] == 120

        # /time/logged got the horizon for free while it selected rows through the repository
        # and summed them in Python. It is a SQL aggregate now (#290), so the predicate has to
        # be asked for by name — pinned here because losing it is silent: the burn-down bar is
        # simply too full, and only for restricted logins.
        logged = (await c.get("/api/v1/time/logged", headers=member_h)).json()
        assert logged["minutes"] == 60
        assert (await c.get("/api/v1/time/logged", headers=owner_h)).json()["minutes"] == 120

        # The list-header tiles ride the same rule: one draft, not two.
        tiles = (await c.get("/api/v1/invoicing/summary", headers=member_h)).json()
        assert tiles["draft_count"] == 1
        owner_tiles = (await c.get("/api/v1/invoicing/summary", headers=owner_h)).json()
        assert owner_tiles["draft_count"] == 2


async def test_horizon_reaches_the_trail_and_the_attachments(client_for) -> None:
    """``(entity_type, entity_id)`` comes straight from the caller (#285).

    The activity feed gated on "may you read this *type*" (audit F7) and the file list on
    nothing at all bar ``company_logo`` — so a restricted membership read the paper trail and
    listed the documents of a client whose every other surface answers 404, and could attach
    new documents to it.
    """
    t, member, membership, owner_h, member_h, a, b, group = await _setup(
        client_for, "horiz-trail", role="admin"
    )

    async with client_for(t.host) as c:
        made = await _seed_for_both(c, owner_h, a, b)
        # Give both clients a trail entry to read.
        for key in ("a", "b"):
            await c.patch(
                f"/api/v1/contacts/{made[key]['contact']['id']}",
                json={"job_title": "Directeur"},
                headers=owner_h,
            )
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204

        # Inside the horizon the panel still works…
        mine = await c.get(
            "/api/v1/activity",
            params={"entity_type": "contact", "entity_id": made["a"]["contact"]["id"]},
            headers=member_h,
        )
        assert mine.status_code == 200 and mine.json() != []
        # …outside it the trail is empty, not the client's history.
        for entity_type, row in (
            ("company", {"id": b["id"]}),
            ("contact", made["b"]["contact"]),
            ("website", made["b"]["website"]),
        ):
            feed = await c.get(
                "/api/v1/activity",
                params={"entity_type": entity_type, "entity_id": row["id"]},
                headers=member_h,
            )
            assert feed.status_code == 200, feed.text
            assert feed.json() == [], entity_type
            files = await c.get(
                "/api/v1/files",
                params={"entity_type": entity_type, "entity_id": row["id"]},
                headers=member_h,
            )
            assert files.status_code == 200 and files.json() == [], entity_type

        # Attaching to an invisible record is refused, like any other write onto one.
        upload = await c.post(
            "/api/v1/files",
            params={"entity_type": "company", "entity_id": b["id"]},
            files={"file": ("note.txt", b"hello", "text/plain")},
            headers=member_h,
        )
        assert upload.status_code == 404, upload.text

        # The owner still reads both trails.
        assert (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "contact", "entity_id": made["b"]["contact"]["id"]},
                headers=owner_h,
            )
        ).json() != []


async def test_no_get_route_mentions_a_client_outside_the_horizon(client_for) -> None:
    """The tripwire (#285): call **every** parameterless ``GET /api/v1`` as a restricted member
    and fail if the response body mentions the client they are scoped away from.

    A per-module test only covers the modules someone remembered. This one covers the next
    module too — and the control run proves it can actually see a leak, so a green result is
    not just "the needle never matched anything".
    """
    from app.main import app

    t, member, membership, owner_h, member_h, a, b, group = await _setup(
        client_for, "horiz-sweep", role="admin"
    )

    async with client_for(t.host) as c:
        await _seed_for_both(c, owner_h, a, b)
        # A recognisable name on the client row itself, so a leak of the company is caught too.
        assert (
            await c.patch(
                f"/api/v1/companies/{b['id']}", json={"name": "ZBEEclient"}, headers=owner_h
            )
        ).status_code == 200
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner_h,
            )
        ).status_code == 204

        # Routers are included lazily, so the paths come from the OpenAPI spec, not app.routes.
        paths = sorted(
            path
            for path, ops in app.openapi()["paths"].items()
            if "get" in ops and path.startswith("/api/v1") and "{" not in path
        )
        assert len(paths) > 100, "the sweep found almost no routes — did the spec shape change?"
        needles = ("ZBEEclient", "Inv-b", "Quo-b", "Sub-b", "Person-b", "klant-b.nl", "host-b")

        leaking, control = [], 0
        for path in paths:
            member_res = await c.get(path, headers=member_h)
            if member_res.status_code == 200 and any(n in member_res.text for n in needles):
                leaking.append(path)
            owner_res = await c.get(path, headers=owner_h)
            if owner_res.status_code == 200 and any(n in owner_res.text for n in needles):
                control += 1
        assert leaking == [], f"these routes leak the invisible client: {leaking}"
        # The control: the owner *does* see that client on plenty of these routes. Without it a
        # green assertion above could mean the seeded rows never reached any response at all.
        assert control >= 5, f"the sweep proved nothing — the owner saw the client on {control}"


async def test_group_trail_is_not_readable_by_the_people_it_restricts(client_for) -> None:
    """Audit F7 on the horizon's own admin surface (#285).

    ``company_group`` opted into the trail without declaring a read permission, so the feed fell
    back to the blanket ``activity.read`` every member holds — and a group's entries name the
    clients moved in and out of it. Reading horizon administration is now
    ``companies.group.manage``, the permission that administers it.
    """
    t, member, membership, owner_h, member_h, a, b, group = await _setup(client_for, "horiz-gtrail")

    async with client_for(t.host) as c:
        # A rename, so there is something in the trail to read.
        assert (
            await c.patch(
                f"/api/v1/companies/groups/{group['id']}",
                json={"name": "Team Zuid"},
                headers=owner_h,
            )
        ).status_code == 200
        params = {"entity_type": "company_group", "entity_id": group["id"]}
        assert (await c.get("/api/v1/activity", params=params, headers=member_h)).status_code == 403
        owner_feed = await c.get("/api/v1/activity", params=params, headers=owner_h)
        assert owner_feed.status_code == 200 and owner_feed.json() != []
