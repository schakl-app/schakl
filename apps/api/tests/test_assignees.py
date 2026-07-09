"""Multiple responsible employees per client / project — one primary, the rest assigned (#12).

Covers the roster round-trip, the one-primary rule, the "my clients"/"my projects" filters
(which must match *any* assignee), the ``responsible_user_id`` mirror that keeps a rolled-back
release working, and tenant isolation on both new join tables (CLAUDE.md §9).
"""

from __future__ import annotations

import uuid

from pwdlib import PasswordHash

from app.core.auth.models import User
from app.core.models import Membership
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant

_ph = PasswordHash.recommended()


async def _add_member(org_id: uuid.UUID, email: str, role: str = "member") -> User:
    """Another employee on the account, returned detached so tests can sign in as them."""
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=_ph.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, org_id)
        session.add(Membership(org_id=org_id, user_id=user.id, role=role))
        await session.commit()
        return User(id=user.id, email=user.email, hashed_password="", is_active=True)


def _ids(assignees: list[dict]) -> list[str]:
    return [a["user_id"] for a in assignees]


def _primary(assignees: list[dict]) -> str | None:
    return next((a["user_id"] for a in assignees if a["is_primary"]), None)


# --- companies ---------------------------------------------------------------- #
async def test_company_roster_round_trip_and_mirrors_responsible(client_for) -> None:
    t = await make_tenant("asg-company")
    owner = str(t.user.id)
    other = str((await _add_member(t.org.id, "other@asg-company.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        created = await c.post(
            "/api/v1/companies",
            json={
                "name": "Acme",
                "assignees": [
                    {"user_id": other, "is_primary": False},
                    {"user_id": owner, "is_primary": True},
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        # Primary first, whatever order it was sent in.
        assert _ids(body["assignees"]) == [owner, other]
        assert _primary(body["assignees"]) == owner
        # The mirror column keeps a rolled-back release reading the right value.
        assert body["responsible_user_id"] == owner

        company_id = body["id"]
        fetched = await c.get(f"/api/v1/companies/{company_id}", headers=headers)
        assert _ids(fetched.json()["assignees"]) == [owner, other]

        # The list endpoint carries the roster too (one batched query, no N+1).
        listed = await c.get("/api/v1/companies", headers=headers)
        assert _ids(listed.json()["items"][0]["assignees"]) == [owner, other]


async def test_unstarred_roster_promotes_the_first(client_for) -> None:
    t = await make_tenant("asg-promote")
    owner = str(t.user.id)
    other = str((await _add_member(t.org.id, "other@asg-promote.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        created = await c.post(
            "/api/v1/companies",
            json={"name": "Acme", "assignees": [{"user_id": other}, {"user_id": owner}]},
            headers=headers,
        )
        assert _primary(created.json()["assignees"]) == other


async def test_patching_assignees_replaces_the_roster(client_for) -> None:
    t = await make_tenant("asg-replace")
    owner = str(t.user.id)
    other = str((await _add_member(t.org.id, "other@asg-replace.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company_id = (
            await c.post(
                "/api/v1/companies",
                json={"name": "Acme", "responsible_user_id": owner},
                headers=headers,
            )
        ).json()["id"]

        patched = await c.patch(
            f"/api/v1/companies/{company_id}",
            json={"assignees": [{"user_id": other, "is_primary": True}]},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert _ids(patched.json()["assignees"]) == [other]
        assert patched.json()["responsible_user_id"] == other

        # And the roster can be emptied.
        cleared = await c.patch(
            f"/api/v1/companies/{company_id}", json={"assignees": []}, headers=headers
        )
        assert cleared.json()["assignees"] == []
        assert cleared.json()["responsible_user_id"] is None


async def test_patching_responsible_alone_moves_the_star_and_keeps_the_others(client_for) -> None:
    """The pre-assignees shape a released web build still posts: it must not wipe the roster."""
    t = await make_tenant("asg-star")
    owner = str(t.user.id)
    other = str((await _add_member(t.org.id, "other@asg-star.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company_id = (
            await c.post(
                "/api/v1/companies",
                json={
                    "name": "Acme",
                    "assignees": [
                        {"user_id": owner, "is_primary": True},
                        {"user_id": other},
                    ],
                },
                headers=headers,
            )
        ).json()["id"]

        patched = await c.patch(
            f"/api/v1/companies/{company_id}",
            json={"responsible_user_id": other},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assignees = patched.json()["assignees"]
        assert _primary(assignees) == other
        assert sorted(_ids(assignees)) == sorted([owner, other])  # nobody was dropped
        assert patched.json()["responsible_user_id"] == other


async def test_two_primaries_are_rejected(client_for) -> None:
    t = await make_tenant("asg-twoprimary")
    owner = str(t.user.id)
    other = str((await _add_member(t.org.id, "other@asg-twoprimary.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        res = await c.post(
            "/api/v1/companies",
            json={
                "name": "Acme",
                "assignees": [
                    {"user_id": owner, "is_primary": True},
                    {"user_id": other, "is_primary": True},
                ],
            },
            headers=headers,
        )
        assert res.status_code == 400
        assert res.json()["error"]["message"] == "errors.multiple_primary_assignees"


async def test_assignee_must_be_a_member_of_this_org(client_for) -> None:
    t = await make_tenant("asg-member")
    stranger = await make_tenant("asg-stranger")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        res = await c.post(
            "/api/v1/companies",
            json={"name": "Acme", "assignees": [{"user_id": str(stranger.user.id)}]},
            headers=headers,
        )
        assert res.status_code == 400
        assert res.json()["error"]["message"] == "errors.invalid_assignee"


# --- projects ----------------------------------------------------------------- #
async def test_project_inherits_only_the_companys_primary(client_for) -> None:
    """A client's roster is a superset of a project's team — inherit the primary, not the list."""
    t = await make_tenant("asg-inherit")
    owner = str(t.user.id)
    other = str((await _add_member(t.org.id, "other@asg-inherit.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company_id = (
            await c.post(
                "/api/v1/companies",
                json={
                    "name": "Acme",
                    "assignees": [
                        {"user_id": owner, "is_primary": True},
                        {"user_id": other},
                    ],
                },
                headers=headers,
            )
        ).json()["id"]

        project = await c.post(
            "/api/v1/projects", json={"name": "Website", "company_id": company_id}, headers=headers
        )
        assert project.status_code == 201, project.text
        assert _ids(project.json()["assignees"]) == [owner]
        assert project.json()["responsible_user_id"] == owner

        # An explicit roster on the project wins over the inheritance.
        explicit = await c.post(
            "/api/v1/projects",
            json={
                "name": "App",
                "company_id": company_id,
                "assignees": [{"user_id": other, "is_primary": True}],
            },
            headers=headers,
        )
        assert _ids(explicit.json()["assignees"]) == [other]

        # And a task under the project inherits the project's primary as its assignee.
        task = await c.post(
            "/api/v1/tasks",
            json={"title": "Kickoff", "project_id": explicit.json()["id"]},
            headers=headers,
        )
        assert task.json()["assignee_user_id"] == other


# --- "my clients" / "my projects" ---------------------------------------------- #
async def test_mine_filter_matches_any_assignee_not_only_the_primary(client_for) -> None:
    t = await make_tenant("asg-mine")
    owner = str(t.user.id)
    other_user = await _add_member(t.org.id, "other@asg-mine.test")
    other = str(other_user.id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        # Owner is primary here, `other` merely assigned.
        shared = (
            await c.post(
                "/api/v1/companies",
                json={
                    "name": "Shared",
                    "assignees": [{"user_id": owner, "is_primary": True}, {"user_id": other}],
                },
                headers=headers,
            )
        ).json()["id"]
        # Owner alone.
        await c.post(
            "/api/v1/companies",
            json={"name": "Mine only", "assignees": [{"user_id": owner, "is_primary": True}]},
            headers=headers,
        )
        # Nobody.
        await c.post("/api/v1/companies", json={"name": "Orphan"}, headers=headers)

        await c.post(
            "/api/v1/projects",
            json={"name": "Shared site", "company_id": shared, "assignees": [{"user_id": other}]},
            headers=headers,
        )

        owner_companies = await c.get("/api/v1/companies?mine=true", headers=headers)
        assert sorted(i["name"] for i in owner_companies.json()["items"]) == [
            "Mine only",
            "Shared",
        ]

        # The non-primary assignee sees the shared client and the project they're on.
        other_headers = await auth_cookie(other_user)
        seen = await c.get("/api/v1/companies?mine=true", headers=other_headers)
        assert [i["name"] for i in seen.json()["items"]] == ["Shared"]
        assert seen.json()["total"] == 1

        projects = await c.get("/api/v1/projects?mine=true", headers=other_headers)
        assert [i["name"] for i in projects.json()["items"]] == ["Shared site"]

        # And the owner, who is on no project, matches none.
        assert (await c.get("/api/v1/projects?mine=true", headers=headers)).json()["items"] == []


# --- tenant isolation (Golden Rule 1) ------------------------------------------ #
async def test_assignees_never_cross_tenants(client_for) -> None:
    a = await make_tenant("asg-iso-a")
    b = await make_tenant("asg-iso-b")

    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        company_id = (
            await c.post(
                "/api/v1/companies",
                json={"name": "A Client", "assignees": [{"user_id": str(a.user.id)}]},
                headers=headers,
            )
        ).json()["id"]
        project_id = (
            await c.post(
                "/api/v1/projects",
                json={"name": "A Project", "assignees": [{"user_id": str(a.user.id)}]},
                headers=headers,
            )
        ).json()["id"]

    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        # Tenant B cannot read tenant A's rows, nor their rosters.
        assert (await c.get(f"/api/v1/companies/{company_id}", headers=headers)).status_code == 404
        assert (await c.get(f"/api/v1/projects/{project_id}", headers=headers)).status_code == 404
        assert (
            await c.patch(
                f"/api/v1/companies/{company_id}",
                json={"assignees": [{"user_id": str(b.user.id)}]},
                headers=headers,
            )
        ).status_code == 404
        # B's own listing never surfaces A's rows, assigned or not.
        assert (await c.get("/api/v1/companies?mine=true", headers=headers)).json()["items"] == []
        assert (await c.get("/api/v1/projects?mine=true", headers=headers)).json()["items"] == []

    # A still sees exactly what it created.
    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        company = await c.get(f"/api/v1/companies/{company_id}", headers=headers)
        assert _ids(company.json()["assignees"]) == [str(a.user.id)]
