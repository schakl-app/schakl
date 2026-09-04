"""tasks module API coverage (CLAUDE.md §6, §9): CRUD, My Day, company panel, isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant, org_today
from tests.test_notifications_fanout import _member


async def test_task_crud_and_status_toggle(client_for) -> None:
    t = await make_tenant("task-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Write plan", "priority": "high"},
            headers=headers,
        )
        assert created.status_code == 201
        task = created.json()
        assert task["status"] == "open"

        done = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers
        )
        assert done.json()["status"] == "done"


async def test_my_open_tasks(client_for) -> None:
    t = await make_tenant("task-mine")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Assigned to me and open → shows in My Day.
        await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Mine", "assignee_user_id": str(t.user.id)},
            headers=headers,
        )
        # A colleague's → excluded from My Day (only tasks assigned to me appear). A task that
        # names nobody is the creator's now (``test_task_assignee_required``), so "not mine"
        # has to be somebody else's.
        other = await _member(t, "collega@example.com")
        await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Theirs", "assignee_user_id": str(other.id)},
            headers=headers,
        )
        # Mine but done → excluded.
        done = await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Done", "assignee_user_id": str(t.user.id)},
            headers=headers,
        )
        await c.patch(
            f"/api/v1/tasks/{done.json()['id']}", json={"status": "done"}, headers=headers
        )

        mine = await c.get("/api/v1/tasks/mine", headers=headers)
        assert mine.status_code == 200
        titles = [row["title"] for row in mine.json()]
        assert titles == ["Mine"]
        compact = await c.get("/api/v1/tasks/dashboard-mine", headers=headers)
        assert compact.status_code == 200
        # A page of rows plus the bucket counts of the whole set (#407) — the tile prints those
        # counts over its partitions, and derived from the page they would be wrong rather than
        # partial for anyone with more open work than the page holds.
        assert compact.json()["total"] == 1
        assert compact.json()["overdue"] == 0
        assert compact.json()["due_today"] == 0
        # Four buckets since #397: this one is due in 2099, which is neither this week nor an
        # "upcoming" that meant the week and the rest at once.
        assert compact.json()["due_week"] == 0
        assert compact.json()["later"] == 1
        assert list(compact.json()["items"][0]) == [
            "id",
            "title",
            "priority",
            "due_date",
            "company_id",
            "company_name",
        ]
        assert compact.json()["items"][0]["title"] == "Mine"
        # The agency's own to-do items belong to no client and are labelled as none.
        assert compact.json()["items"][0]["company_name"] is None


async def test_dashboard_mine_names_the_client(client_for, count_queries) -> None:
    """A tile row says whose work it is — through the project when it only names one."""
    t = await make_tenant("task-mine-client")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        direct = (
            await c.post("/api/v1/companies", json={"name": "Bakkerij Jansen"}, headers=headers)
        ).json()
        via = (
            await c.post("/api/v1/companies", json={"name": "Garage Peters"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Website", "company_id": via["id"]},
                headers=headers,
            )
        ).json()
        await c.post(
            "/api/v1/tasks",
            json={
                "due_date": FAR_FUTURE_DUE,
                "title": "Op de klant",
                "assignee_user_id": str(t.user.id),
                "company_id": direct["id"],
            },
            headers=headers,
        )
        await c.post(
            "/api/v1/tasks",
            json={
                "due_date": FAR_FUTURE_DUE,
                "title": "Op het project",
                "assignee_user_id": str(t.user.id),
                "project_id": project["id"],
            },
            headers=headers,
        )

        # One query for the rows however many clients they span (docs/PERFORMANCE.md); the
        # bucket counts ride a second, grouped statement, never one per bucket (#407).
        with count_queries() as counted:
            res = await c.get("/api/v1/tasks/dashboard-mine", headers=headers)
        assert res.status_code == 200
        named = {
            row["title"]: (row["company_id"], row["company_name"]) for row in res.json()["items"]
        }
        assert named["Op de klant"] == (direct["id"], "Bakkerij Jansen")
        assert named["Op het project"] == (via["id"], "Garage Peters")
        assert len(counted.matching("from tasks")) == 2


async def test_dashboard_groups_are_compact_and_exclude_terminal_tasks(client_for) -> None:
    t = await make_tenant("task-dashboard-groups")
    headers = await auth_cookie(t.user)
    overdue = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Grouped Co"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Grouped Project", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        await c.post(
            "/api/v1/tasks",
            json={"title": "Late project task", "project_id": project["id"], "due_date": overdue},
            headers=headers,
        )
        finished = (
            await c.post(
                "/api/v1/tasks",
                json={"due_date": FAR_FUTURE_DUE, "title": "Finished", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        await c.patch(
            f"/api/v1/tasks/{finished['id']}",
            json={"status": "done"},
            headers=headers,
        )

        response = await c.get("/api/v1/tasks/dashboard-groups", headers=headers)
        assert response.status_code == 200
        # A page of groups plus how many exist (#407): this GROUP BY had no LIMIT, and the tile
        # rendered every row it produced. The four figures are #398's urgency partition.
        assert response.json() == {
            "items": [
                {
                    "entity_type": "project",
                    "entity_id": project["id"],
                    "label": "Grouped Project",
                    # A project row names its client too: two clients may each run a "Website",
                    # and the tile drew them as two identical rows (issue #15).
                    "company_id": company["id"],
                    "company_name": "Grouped Co",
                    "count": 1,
                    "overdue": 1,
                    "due_today": 0,
                    "due_week": 0,
                }
            ],
            "total": 1,
        }


async def test_dashboard_group_without_client_or_project_is_addressable(client_for) -> None:
    """The tile's fallback bucket has a list to open: ``?unlinked=1``, not a bare /tasks."""
    t = await make_tenant("task-unlinked")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Someone"}, headers=headers)
        ).json()
        await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Client work", "company_id": company["id"]},
            headers=headers,
        )
        await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Loose end"},
            headers=headers,
        )

        groups = (await c.get("/api/v1/tasks/dashboard-groups", headers=headers)).json()["items"]
        loose = [g for g in groups if g["entity_type"] == "none"]
        assert len(loose) == 1
        assert loose[0]["entity_id"] is None
        assert loose[0]["label"] is None
        assert loose[0]["company_name"] is None
        assert loose[0]["count"] == 1

        filtered = await c.get("/api/v1/tasks?unlinked=true", headers=headers)
        assert filtered.status_code == 200
        assert [row["title"] for row in filtered.json()["items"]] == ["Loose end"]
        assert filtered.json()["total"] == loose[0]["count"]

        # Absent means "any client", which is the question the bucket is *not* asking.
        assert (await c.get("/api/v1/tasks", headers=headers)).json()["total"] == 2


async def test_tasks_panel_on_company(client_for) -> None:
    t = await make_tenant("task-panel")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Panel Co"}, headers=headers)
        ).json()
        await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "For company", "company_id": company["id"]},
            headers=headers,
        )
        panels = {
            p["key"]: p
            for p in (
                await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
            ).json()
        }
        assert "tasks.company" in panels
        assert panels["tasks.company"]["data"]["tasks"][0]["title"] == "For company"


async def test_position_assigned_and_reorder(client_for) -> None:
    t = await make_tenant("task-order")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        first = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "First"},
            headers=headers,
        )).json()
        second = (
            await c.post(
                "/api/v1/tasks",
                json={"due_date": FAR_FUTURE_DUE, "title": "Second"},
                headers=headers,
            )
        ).json()
        assert second["position"] > first["position"]

        # Fractional-midpoint reorder: move Second before First.
        await c.patch(
            f"/api/v1/tasks/{second['id']}",
            json={"position": first["position"] - 1},
            headers=headers,
        )
        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        assert [row["title"] for row in listed] == ["Second", "First"]


async def test_completed_at_set_and_cleared(client_for) -> None:
    t = await make_tenant("task-completed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "T"},
            headers=headers,
        )).json()
        assert task["completed_at"] is None

        done = (
            await c.patch(
                f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers
            )
        ).json()
        assert done["completed_at"] is not None

        reopened = (
            await c.patch(
                f"/api/v1/tasks/{task['id']}", json={"status": "open"}, headers=headers
            )
        ).json()
        assert reopened["completed_at"] is None


async def test_due_filters(client_for) -> None:
    from datetime import timedelta

    
    t = await make_tenant("task-due")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/tasks",
            json={"title": "Late", "due_date": (today - timedelta(days=2)).isoformat()},
            headers=headers,
        )
        await c.post(
            "/api/v1/tasks",
            json={"title": "Today", "due_date": today.isoformat()},
            headers=headers,
        )
        await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Sometime"},
            headers=headers,
        )

        overdue = (
            await c.get("/api/v1/tasks", params={"due": "overdue"}, headers=headers)
        ).json()
        assert [row["title"] for row in overdue["items"]] == ["Late"]

        due_today = (
            await c.get("/api/v1/tasks", params={"due": "today"}, headers=headers)
        ).json()
        assert [row["title"] for row in due_today["items"]] == ["Today"]


async def test_task_detail_shape(client_for) -> None:
    t = await make_tenant("task-detail")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"due_date": FAR_FUTURE_DUE, "title": "Card", "description": "Body"},
                headers=headers,
            )
        ).json()
        await c.post(
            f"/api/v1/tasks/{task['id']}/comments", json={"body": "Hello"}, headers=headers
        )

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert detail["description"] == "Body"
        assert detail["labels"] == []
        assert detail["checklists"] == []
        assert [comment["body"] for comment in detail["comments"]] == ["Hello"]
        assert {a["action"] for a in detail["activities"]} >= {"created", "commented"}

        # List rows carry the comment count aggregate.
        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        assert listed[0]["comment_count"] == 1


async def _company_with_contact(c, headers, *, company: str, contact: str) -> tuple[str, str]:
    """A company and a contact linked to it — the shape a contact assignee (#273) needs."""
    co = (await c.post("/api/v1/companies", json={"name": company}, headers=headers)).json()
    ct = (
        await c.post(
            "/api/v1/contacts",
            json={"first_name": contact, "company_ids": [co["id"]]},
            headers=headers,
        )
    ).json()
    return co["id"], ct["id"]


async def test_assign_contact_of_own_client(client_for) -> None:
    """A task can be assigned to a contact of its own client company, exclusive with an employee."""
    t = await make_tenant("task-contact-ok")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id, contact_id = await _company_with_contact(
            c, headers, company="Acme", contact="Klaas"
        )
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Wait on client",
                    "company_id": company_id,
                },
                headers=headers,
            )
        ).json()

        assigned = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"assignee_user_id": None, "assignee_contact_id": contact_id},
            headers=headers,
        )
        assert assigned.status_code == 200
        body = assigned.json()
        assert body["assignee_contact_id"] == contact_id
        # A contact assignee never coexists with an employee one.
        assert body["assignee_user_id"] is None

        # The list endpoint filters on the contact assignee.
        listed = (
            await c.get(
                "/api/v1/tasks",
                params={"assignee_contact_id": contact_id},
                headers=headers,
            )
        ).json()
        assert [row["id"] for row in listed["items"]] == [task["id"]]

        # …and a contact assignee stays out of employee-only "My Day" (no user id to key on).
        assert (await c.get("/api/v1/tasks/mine", headers=headers)).json() == []


async def test_assign_contact_at_create(client_for) -> None:
    """Creating with a contact assignee keeps it — the client's responsible employee never
    overwrites a deliberate contact choice."""
    t = await make_tenant("task-contact-create")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id, contact_id = await _company_with_contact(
            c, headers, company="Acme", contact="Klaas"
        )
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Client to send assets",
                    "company_id": company_id,
                    "assignee_contact_id": contact_id,
                },
                headers=headers,
            )
        ).json()
        assert task["assignee_contact_id"] == contact_id
        assert task["assignee_user_id"] is None


async def test_assignee_kinds_are_exclusive(client_for) -> None:
    t = await make_tenant("task-contact-excl")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id, contact_id = await _company_with_contact(
            c, headers, company="Acme", contact="Klaas"
        )
        both = await c.post(
            "/api/v1/tasks",
            json={
                "due_date": FAR_FUTURE_DUE,
                "title": "Both",
                "company_id": company_id,
                "assignee_user_id": str(t.user.id),
                "assignee_contact_id": contact_id,
            },
            headers=headers,
        )
        assert both.status_code == 422
        assert both.json()["error"]["fields"]["assignee_contact_id"] == (
            "errors.tasks_assignee_conflict"
        )


async def test_contact_assignee_needs_a_client(client_for) -> None:
    """An internal task (no company) has no client to draw a contact from."""
    t = await make_tenant("task-contact-noco")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _company_id, contact_id = await _company_with_contact(
            c, headers, company="Acme", contact="Klaas"
        )
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"due_date": FAR_FUTURE_DUE, "title": "Internal"},
                headers=headers,
            )
        ).json()
        # Posted the way the picker posts it — the contact beside an explicitly empty roster —
        # because the internal task is its creator's (somebody is always on a task), and a
        # contact *beside* an employee is the exclusivity refusal rather than this one.
        rejected = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"assignee_contact_id": contact_id, "assignees": []},
            headers=headers,
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["fields"]["assignee_contact_id"] == (
            "errors.tasks_assignee_contact_company"
        )


async def test_contact_assignee_company_isolation(client_for) -> None:
    """The tenant-isolation-shaped part: a contact of another company (same org) and a contact of
    another org are both refused as a task assignee — never just filtered from the picker."""
    t = await make_tenant("task-contact-iso")
    headers = await auth_cookie(t.user)
    other = await make_tenant("task-contact-iso-other")
    other_headers = await auth_cookie(other.user)

    async with client_for(t.host) as c:
        client_co, _client_ct = await _company_with_contact(
            c, headers, company="Client", contact="Ours"
        )
        # A contact linked to a *different* company in the same org.
        _elsewhere_co, elsewhere_ct = await _company_with_contact(
            c, headers, company="Elsewhere", contact="Theirs"
        )
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"due_date": FAR_FUTURE_DUE, "title": "For client", "company_id": client_co},
                headers=headers,
            )
        ).json()

        # ``assignees: []`` beside the contact, as the picker posts it: the task is its creator's
        # until then, and a contact beside an employee is the other refusal.
        wrong_company = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"assignee_contact_id": elsewhere_ct, "assignees": []},
            headers=headers,
        )
        assert wrong_company.status_code == 422
        assert wrong_company.json()["error"]["fields"]["assignee_contact_id"] == (
            "errors.tasks_assignee_contact_company"
        )

    # A contact belonging to another tenant entirely.
    async with client_for(other.host) as co:
        _foreign_co, foreign_ct = await _company_with_contact(
            co, other_headers, company="Foreign", contact="Alien"
        )

    async with client_for(t.host) as c:
        cross_org = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"assignee_contact_id": foreign_ct, "assignees": []},
            headers=headers,
        )
        assert cross_org.status_code == 422
        assert cross_org.json()["error"]["fields"]["assignee_contact_id"] == (
            "errors.tasks_assignee_contact_company"
        )
        # The task was never actually reassigned.
        assert (
            await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)
        ).json()["assignee_contact_id"] is None


async def test_rehoming_task_rejects_orphaned_contact_assignee(client_for) -> None:
    """Moving a task to another client while it still holds the first client's contact is refused,
    rather than silently leaving an unrelated client's contact assigned."""
    t = await make_tenant("task-contact-rehome")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        co_a, ct_a = await _company_with_contact(c, headers, company="A", contact="Ann")
        co_b, _ct_b = await _company_with_contact(c, headers, company="B", contact="Bob")
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "T",
                    "company_id": co_a,
                    "assignee_contact_id": ct_a,
                },
                headers=headers,
            )
        ).json()
        moved = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"company_id": co_b}, headers=headers
        )
        assert moved.status_code == 422
        assert moved.json()["error"]["fields"]["assignee_contact_id"] == (
            "errors.tasks_assignee_contact_company"
        )


async def test_tasks_tenant_isolation(client_for) -> None:
    a = await make_tenant("task-iso-a")
    b = await make_tenant("task-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        created = await ca.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Secret"},
            headers=a_headers,
        )
        a_task_id = created.json()["id"]

    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/tasks", headers=b_headers)).json()["total"] == 0
        assert (
            await cb.get(f"/api/v1/tasks/{a_task_id}", headers=b_headers)
        ).status_code == 404


# --------------------------------------------------------------------------- #
# The tile ranks by urgency, not by volume (#398)
# --------------------------------------------------------------------------- #
async def _group_task(c, headers, *, title, company_id, due) -> None:
    res = await c.post(
        "/api/v1/tasks",
        json={"title": title, "company_id": company_id, "due_date": due},
        headers=headers,
    )
    assert res.status_code == 201, res.text


async def test_dashboard_groups_rank_by_urgency_and_count_each_bucket(
    client_for, count_queries
) -> None:
    """A client with something late outranks a client with five comfortable tasks (#398).

    The complaint the team wrote is that a bare count says nothing about urgency, and the tile
    was *ordered* on that bare count - so the one row carrying an overdue task sat at position
    eleven, below six rows with nothing late on them at all.
    """
    t = await make_tenant("task-groups-urgency")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        busy = (
            await c.post("/api/v1/companies", json={"name": "Bulk BV"}, headers=headers)
        ).json()
        late = (
            await c.post("/api/v1/companies", json={"name": "Achterstand BV"}, headers=headers)
        ).json()
        soon = (
            await c.post("/api/v1/companies", json={"name": "Vandaag BV"}, headers=headers)
        ).json()

        # Five comfortable tasks: the row that used to be first.
        for n in range(5):
            await _group_task(
                c, headers, title=f"Rustig {n}", company_id=busy["id"], due=FAR_FUTURE_DUE
            )
        # One task, due last Tuesday: the row that used to be eleventh.
        await _group_task(
            c,
            headers,
            title="Te laat",
            company_id=late["id"],
            due=(today - timedelta(days=6)).isoformat(),
        )
        # One today plus one inside the week: second, above volume and below lateness.
        await _group_task(
            c, headers, title="Vandaag", company_id=soon["id"], due=today.isoformat()
        )
        await _group_task(
            c,
            headers,
            title="Deze week",
            company_id=soon["id"],
            due=(today + timedelta(days=3)).isoformat(),
        )

        # One grouped query for the whole tile, four aggregates and the group total included
        # (docs/PERFORMANCE.md - a budget test, because the shape is invisible in the JSON).
        with count_queries() as counted:
            res = await c.get("/api/v1/tasks/dashboard-groups", headers=headers)
        assert res.status_code == 200
        assert len(counted.matching("from tasks")) == 1

        body = res.json()
        rows = body["items"]
        assert [row["label"] for row in rows] == ["Achterstand BV", "Vandaag BV", "Bulk BV"]
        assert body["total"] == 3

        by_name = {row["label"]: row for row in rows}
        assert (by_name["Achterstand BV"]["overdue"], by_name["Achterstand BV"]["count"]) == (1, 1)
        assert by_name["Vandaag BV"]["due_today"] == 1
        assert by_name["Vandaag BV"]["due_week"] == 1
        assert by_name["Vandaag BV"]["overdue"] == 0
        # Far-future work is in none of the three buckets: a row with nothing urgent on it
        # draws no counters at all, which is what keeps the tile scannable.
        assert by_name["Bulk BV"]["count"] == 5
        assert (by_name["Bulk BV"]["overdue"], by_name["Bulk BV"]["due_today"]) == (0, 0)
        assert by_name["Bulk BV"]["due_week"] == 0


async def test_every_dashboard_counter_opens_exactly_what_it_counted(client_for) -> None:
    """docs/UX.md principle 7, made literal: the four figures and their four lists agree.

    The three buckets are a *partition*, which is why ``?due=week`` is the seven days after
    today rather than including it - a counter beside "Vandaag" that opened a superset of it
    would be a figure the reader cannot take apart.
    """
    t = await make_tenant("task-groups-links")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        for title, due in (
            ("Te laat", today - timedelta(days=1)),
            ("Vandaag", today),
            ("Overmorgen", today + timedelta(days=2)),
            ("Laatste dag", today + timedelta(days=7)),
            ("Net erbuiten", today + timedelta(days=8)),
        ):
            await _group_task(
                c, headers, title=title, company_id=company["id"], due=due.isoformat()
            )

        row = (await c.get("/api/v1/tasks/dashboard-groups", headers=headers)).json()["items"][0]
        assert (row["overdue"], row["due_today"], row["due_week"], row["count"]) == (1, 1, 2, 5)

        base = f"/api/v1/tasks?company_id={company['id']}"
        for bucket, due in (("overdue", "overdue"), ("due_today", "today"), ("due_week", "week")):
            listed = await c.get(f"{base}&due={due}", headers=headers)
            assert listed.status_code == 200, listed.text
            assert listed.json()["total"] == row[bucket], due
        assert (await c.get(base, headers=headers)).json()["total"] == row["count"]

        # The eighth day is outside the week on purpose, and today is outside it too.
        week = await c.get(f"{base}&due=week", headers=headers)
        assert sorted(item["title"] for item in week.json()["items"]) == [
            "Laatste dag",
            "Overmorgen",
        ]


async def test_dashboard_groups_are_capped_and_say_how_many_are_not_shown(client_for) -> None:
    """A tile listing every project an agency runs is a scroll, not a summary (CLAUDE.md §17)."""
    t = await make_tenant("task-groups-cap")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for n in range(12):
            company = (
                await c.post(
                    "/api/v1/companies", json={"name": f"Klant {n:02d}"}, headers=headers
                )
            ).json()
            await _group_task(
                c, headers, title=f"Werk {n}", company_id=company["id"], due=FAR_FUTURE_DUE
            )

        body = (await c.get("/api/v1/tasks/dashboard-groups?limit=5", headers=headers)).json()
        assert len(body["items"]) == 5
        # Not "5": the remainder is what the tile has to be able to say out loud, and a short
        # list that cannot is one that reads as complete.
        assert body["total"] == 12
