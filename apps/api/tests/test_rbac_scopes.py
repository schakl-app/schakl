"""Scope qualifiers (`:own` / `:any`) across tasks, time and leave (issue #19, #52).

The three rules the plan says must not regress, each named in its own test, plus the answer to
#12 (a non-primary assignee may write the task assigned to them, and nothing else).
"""

from __future__ import annotations

from tests.conftest import auth_cookie, leave_workday, make_tenant
from tests.test_task_subresources import add_member


# --------------------------------------------------------------------------- #
# time: 404 vs 403, the approved lock, and the budget-bar branch
# --------------------------------------------------------------------------- #
async def test_another_users_time_entry_is_404_not_403(client_for) -> None:
    """`_owned_or_404` hides existence. A generic 403-raising helper would leak it on every
    get/update/delete-by-id."""
    tenant = await make_tenant("scope-404")
    owner_headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        owners_entry = (
            await client.post(
                "/api/v1/time/entries",
                json={"started_at": "2026-01-05T09:00:00Z", "minutes": 60},
                headers=owner_headers,
            )
        ).json()

        for method in ("get", "patch", "delete"):
            response = await client.request(
                method.upper(),
                f"/api/v1/time/entries/{owners_entry['id']}",
                headers=member_headers,
                json={"minutes": 30} if method == "patch" else None,
            )
            assert response.status_code == 404, (
                f"{method} leaked existence with {response.status_code}"
            )


async def test_an_approved_entry_is_locked_for_its_owner_too(client_for) -> None:
    """`_ensure_not_locked` is a capability (`time.entry.approve`), not a scope: 403, not 404,
    and the owner is locked out of their own signed-off hours."""
    tenant = await make_tenant("scope-locked")
    owner_headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        entry = (
            await client.post(
                "/api/v1/time/entries",
                json={"started_at": "2026-01-05T09:00:00Z", "minutes": 60},
                headers=member_headers,
            )
        ).json()
        approved = await client.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": [entry["id"]], "approved": True},
            headers=owner_headers,
        )
        assert approved.status_code == 200

        locked = await client.patch(
            f"/api/v1/time/entries/{entry['id']}", json={"minutes": 30}, headers=member_headers
        )
        assert locked.status_code == 403
        assert locked.json()["error"]["message"] == "errors.approved_locked"

        # Whoever may approve may still correct it.
        assert (
            await client.patch(
                f"/api/v1/time/entries/{entry['id']}",
                json={"minutes": 30},
                headers=owner_headers,
            )
        ).status_code == 200


async def test_a_member_keeps_budget_bars_but_not_the_org_wide_report(client_for) -> None:
    """`/time/logged` and an entity-scoped `/time/entries?all_users=true` are team-visible; the
    unscoped `all_users` report is not. Narrowing the route to `:any` would silently remove every
    member's budget bar."""
    tenant = await make_tenant("scope-budget")
    owner_headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        company = (
            await client.post("/api/v1/companies", json={"name": "Acme"}, headers=owner_headers)
        ).json()
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Site", "company_id": company["id"]},
                headers=owner_headers,
            )
        ).json()

        logged = await client.get(
            f"/api/v1/time/logged?project_id={project['id']}", headers=member_headers
        )
        assert logged.status_code == 200

        scoped = await client.get(
            f"/api/v1/time/entries?project_id={project['id']}&all_users=true",
            headers=member_headers,
        )
        assert scoped.status_code == 200

        unscoped = await client.get("/api/v1/time/entries?all_users=true", headers=member_headers)
        assert unscoped.status_code == 403

        assert (
            await client.get("/api/v1/time/report", headers=member_headers)
        ).status_code == 403
        assert (
            await client.get("/api/v1/time/report", headers=owner_headers)
        ).status_code == 200


# --------------------------------------------------------------------------- #
# tasks: #12 — own means assignee
# --------------------------------------------------------------------------- #
async def test_a_member_may_edit_the_task_assigned_to_them_and_no_other(client_for) -> None:
    """The answer to #12. `tasks.task.write:own` where *own* = assignee. This rule did not exist
    before: tasks were a flat ensure_can_write() and any staff member could edit any task."""
    tenant = await make_tenant("scope-tasks")
    owner_headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        mine = (
            await client.post(
                "/api/v1/tasks",
                json={"title": "Mine", "assignee_user_id": str(member.id)},
                headers=owner_headers,
            )
        ).json()
        theirs = (
            await client.post(
                "/api/v1/tasks",
                json={"title": "Theirs", "assignee_user_id": str(tenant.user.id)},
                headers=owner_headers,
            )
        ).json()

        assert (
            await client.patch(
                f"/api/v1/tasks/{mine['id']}", json={"title": "Mine, edited"},
                headers=member_headers,
            )
        ).status_code == 200
        refused = await client.patch(
            f"/api/v1/tasks/{theirs['id']}", json={"title": "Nope"}, headers=member_headers
        )
        assert refused.status_code == 403

        # Reading either is fine; both tasks are org-visible, so 403 leaks nothing.
        assert (
            await client.get(f"/api/v1/tasks/{theirs['id']}", headers=member_headers)
        ).status_code == 200

        # Creating is its own permission, held by member.
        assert (
            await client.post("/api/v1/tasks", json={"title": "New"}, headers=member_headers)
        ).status_code == 201
        # Deleting is not.
        assert (
            await client.delete(f"/api/v1/tasks/{mine['id']}", headers=member_headers)
        ).status_code == 403


async def test_a_member_may_delete_their_own_comment_but_not_anothers(client_for) -> None:
    tenant = await make_tenant("scope-comments")
    owner_headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        task = (
            await client.post("/api/v1/tasks", json={"title": "T"}, headers=owner_headers)
        ).json()
        theirs = (
            await client.post(
                f"/api/v1/tasks/{task['id']}/comments", json={"body": "owner"},
                headers=owner_headers,
            )
        ).json()
        mine = (
            await client.post(
                f"/api/v1/tasks/{task['id']}/comments", json={"body": "member"},
                headers=member_headers,
            )
        ).json()

        assert (
            await client.delete(
                f"/api/v1/tasks/{task['id']}/comments/{theirs['id']}", headers=member_headers
            )
        ).status_code == 403
        assert (
            await client.delete(
                f"/api/v1/tasks/{task['id']}/comments/{mine['id']}", headers=member_headers
            )
        ).status_code == 204
        # `tasks.comment.write:any` lets a manager clean up anyone's.
        assert (
            await client.delete(
                f"/api/v1/tasks/{task['id']}/comments/{theirs['id']}", headers=owner_headers
            )
        ).status_code == 204


# --------------------------------------------------------------------------- #
# leave
# --------------------------------------------------------------------------- #
async def test_leave_scopes(client_for) -> None:
    tenant = await make_tenant("scope-leave")
    owner_headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        types = (await client.get("/api/v1/leave/types", headers=owner_headers)).json()
        # Needs approval, tracks no balance — a pending request without seeding entitlements.
        special = next(t for t in types if t["key"] == "special")

        # A future working day: a member holds `leave.request.write:own`, which may not backdate
        # into the closed calendar (#65), and the server computes the hours from the schedule (§14).
        workday = leave_workday()
        request = await client.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": special["id"],
                "start_date": workday.isoformat(),
                "end_date": workday.isoformat(),
            },
            headers=member_headers,
        )
        assert request.status_code == 201, request.text
        request_id = request.json()["id"]

        # A member may not approve their own request, nor pull the org-wide list.
        assert (
            await client.post(
                f"/api/v1/leave/requests/{request_id}/decide",
                json={"approved": True},
                headers=member_headers,
            )
        ).status_code == 403
        assert (
            await client.get("/api/v1/leave/requests?all_users=true", headers=member_headers)
        ).status_code == 403
        # …but the team calendar is staff-visible.
        assert (
            await client.get(
                f"/api/v1/leave/team?date_from={workday.isoformat()}&date_to={workday.isoformat()}",
                headers=member_headers,
            )
        ).status_code == 200

        assert (
            await client.post(
                f"/api/v1/leave/requests/{request_id}/decide",
                json={"approved": True},
                headers=owner_headers,
            )
        ).status_code == 200


async def test_a_leave_request_of_another_user_is_404(client_for) -> None:
    tenant = await make_tenant("scope-leave-404")
    owner_headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        types = (await client.get("/api/v1/leave/types", headers=owner_headers)).json()
        special = next(t for t in types if t["key"] == "special")
        owners_request = (
            await client.post(
                "/api/v1/leave/requests",
                json={
                    "leave_type_id": special["id"],
                    "start_date": "2026-04-06",
                    "end_date": "2026-04-06",
                    "hours": "8",
                },
                headers=owner_headers,
            )
        ).json()
        assert (
            await client.get(
                f"/api/v1/leave/requests/{owners_request['id']}", headers=member_headers
            )
        ).status_code == 404


# --------------------------------------------------------------------------- #
# people-pickers
# --------------------------------------------------------------------------- #
async def test_permission_filtered_lookup_is_one_query_and_deduplicates(
    client_for, count_queries
) -> None:
    tenant = await make_tenant("scope-picker")
    headers = await auth_cookie(tenant.user)
    await add_member(tenant, name="Plain Member")

    def _picker_statements(counter) -> int:
        """The picker's own SELECT: it is the only one ordering by a nulls-last display name."""
        return len(counter.matching("NULLS LAST"))

    async with client_for(tenant.host) as client:
        everyone = await client.get("/api/v1/members/lookup", headers=headers)
        assert len(everyone.json()) == 2

        with count_queries() as counter:
            approvers = await client.get(
                "/api/v1/members/lookup?permission=leave.request.approve", headers=headers
            )
        assert approvers.status_code == 200
        # Only the owner may approve; the member holds no leave.request.approve.
        assert [row["email"] for row in approvers.json()] == [tenant.user.email]
        assert _picker_statements(counter) == 1

        assignees = await client.get(
            "/api/v1/members/lookup?permission=tasks.task.write", headers=headers
        )
        # `:own` counts — a member may be assigned a task.
        assert len(assignees.json()) == 2

        # Adding people does not add statements (no N+1), and a user holding two granting roles
        # still appears once (the DISTINCT in permission_holder_ids).
        for index in range(3):
            await add_member(tenant, name=f"Extra {index}")
        with count_queries() as counter:
            more = await client.get(
                "/api/v1/members/lookup?permission=tasks.task.write", headers=headers
            )
        assert len(more.json()) == 5
        assert len({row["user_id"] for row in more.json()}) == 5
        assert _picker_statements(counter) == 1

        assert (
            await client.get("/api/v1/members/lookup?permission=not.a.thing", headers=headers)
        ).status_code == 422
