"""Verantwoordelijke (responsible user) per client (CLAUDE.md §14 adjacency).

A client's responsible defaults down onto new projects, and a project's/company's responsible
seeds new tasks' assignee — always overridable. Also covers the round-trip on companies.
"""

from __future__ import annotations

import uuid

from pwdlib import PasswordHash

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant

_ph = PasswordHash.recommended()


async def _add_member(org_id: uuid.UUID, email: str, role: str = "member") -> uuid.UUID:
    """A second org member to override defaults with."""
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
        await add_membership(session, org_id, user.id, role)
        await session.commit()
        return user.id


async def test_responsible_defaults_down_to_project_and_task(client_for) -> None:
    t = await make_tenant("resp-default")
    uid = str(t.user.id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        company = await c.post(
            "/api/v1/companies",
            json={"name": "Acme", "responsible_user_id": uid},
            headers=headers,
        )
        assert company.status_code == 201, company.text
        assert company.json()["responsible_user_id"] == uid
        company_id = company.json()["id"]

        # Project inherits the client's responsible when none is provided.
        project = await c.post(
            "/api/v1/projects",
            json={"name": "Website", "company_id": company_id},
            headers=headers,
        )
        assert project.status_code == 201
        assert project.json()["responsible_user_id"] == uid
        project_id = project.json()["id"]

        # Task inherits it as the assignee (from the project).
        task = await c.post(
            "/api/v1/tasks",
            json={"title": "Kickoff", "project_id": project_id, "company_id": company_id},
            headers=headers,
        )
        assert task.status_code == 201
        assert task.json()["assignee_user_id"] == uid


async def test_responsible_and_assignee_overrides_win(client_for) -> None:
    t = await make_tenant("resp-override")
    owner = str(t.user.id)
    other = str(await _add_member(t.org.id, "other@example.com"))
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        company = await c.post(
            "/api/v1/companies",
            json={"name": "Acme", "responsible_user_id": owner},
            headers=headers,
        )
        company_id = company.json()["id"]

        # Explicit project responsible overrides the company default…
        project = await c.post(
            "/api/v1/projects",
            json={"name": "Site", "company_id": company_id, "responsible_user_id": other},
            headers=headers,
        )
        assert project.json()["responsible_user_id"] == other
        project_id = project.json()["id"]

        # …and an explicit task assignee overrides the project's responsible.
        task = await c.post(
            "/api/v1/tasks",
            json={"title": "Design", "project_id": project_id, "assignee_user_id": owner},
            headers=headers,
        )
        assert task.json()["assignee_user_id"] == owner


async def test_no_parent_responsible_means_no_default(client_for) -> None:
    t = await make_tenant("resp-none")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        assert company.json()["responsible_user_id"] is None
        company_id = company.json()["id"]
        task = await c.post(
            "/api/v1/tasks",
            json={"title": "Loose", "company_id": company_id},
            headers=headers,
        )
        assert task.json()["assignee_user_id"] is None
