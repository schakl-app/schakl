"""Tenant-isolation test for the companies module (CLAUDE.md §9, Golden Rule 1).

Proves the boundary holds at **both** layers, independently:
  * Postgres RLS — a raw query bound to org A never sees org B's rows, and an unbound
    connection (no GUC) sees nothing (fail closed);
  * the application repository — a repo scoped to org A never returns org B's row even when
    RLS *would* allow it (GUC bound to B), so a forgotten GUC can't leak either.
Plus the same guarantee through the HTTP API across tenant hostnames.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.core.tenancy import TenantScopedRepository
from app.db import async_session_maker, set_current_org
from app.modules.companies.models import Company
from tests.conftest import auth_cookie, make_tenant


async def _make_company(org_id: uuid.UUID, name: str) -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        company = Company(org_id=org_id, name=name)
        session.add(company)
        await session.commit()
        return company.id


async def test_rls_isolates_and_fails_closed() -> None:
    a = await make_tenant("iso-a")
    b = await make_tenant("iso-b")
    a_company = await _make_company(a.org.id, "Alpha Co")
    b_company = await _make_company(b.org.id, "Beta Co")

    # RLS: bound to A, only A's row is visible; B's row can't be fetched by id.
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        visible = (await session.execute(text("SELECT id FROM companies"))).scalars().all()
        assert visible == [a_company]
        leaked = (
            await session.execute(
                text("SELECT id FROM companies WHERE id = :id"), {"id": str(b_company)}
            )
        ).scalars().all()
        assert leaked == []

    # Fail closed: no GUC bound → RLS returns nothing.
    async with async_session_maker() as session:
        rows = (await session.execute(text("SELECT id FROM companies"))).scalars().all()
        assert rows == []


async def test_repository_filter_holds_even_when_rls_would_allow() -> None:
    a = await make_tenant("repo-a")
    b = await make_tenant("repo-b")
    b_company = await _make_company(b.org.id, "Beta Co")

    # GUC bound to B (RLS would expose B's row), but a repo scoped to A must not see it.
    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        repo_a = TenantScopedRepository(session, a.org.id, Company)
        assert await repo_a.get(b_company) is None
        assert await repo_a.count() == 0


async def test_api_cross_tenant_isolation(client_for) -> None:
    a = await make_tenant("apiiso-a")
    b = await make_tenant("apiiso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    # A creates a company on its own host.
    async with client_for(a.host) as ca:
        created = await ca.post(
            "/api/v1/companies", json={"name": "A Corp"}, headers=a_headers
        )
        assert created.status_code == 201
        a_company_id = created.json()["id"]

    # B, on its own host, sees none of A's data.
    async with client_for(b.host) as cb:
        listing = await cb.get("/api/v1/companies", headers=b_headers)
        assert listing.status_code == 200
        assert listing.json()["total"] == 0

        fetch = await cb.get(f"/api/v1/companies/{a_company_id}", headers=b_headers)
        assert fetch.status_code == 404

    # A's *valid* session against B's host → 403 (A is not a member of B).
    async with client_for(b.host) as cx:
        r = await cx.get("/api/v1/companies", headers=a_headers)
        assert r.status_code == 403
        assert r.json()["error"]["message"] == "errors.forbidden"
