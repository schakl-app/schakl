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
from tests.conftest import add_membership, auth_cookie, make_tenant


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

    # A's session against B's host → 401. It is a valid *token*, but not a session here: a
    # session is minted for one org and names it (`core/auth/backend.py`), so the request is
    # unauthenticated on B before the question of A's membership in B is ever reached.
    async with client_for(b.host) as cx:
        r = await cx.get("/api/v1/companies", headers=a_headers)
        assert r.status_code == 401
        assert r.json()["error"]["message"] == "errors.unauthorized"


async def test_session_is_minted_for_one_org(client_for) -> None:
    """The membership check is not the only thing standing between tenants (the session-minting
    hole): a member of *both* orgs still cannot carry one org's cookie onto the other's host,
    and an org-less session — what the cloud console's apex mints — reaches no tenant at all."""
    a = await make_tenant("mint-a")
    b = await make_tenant("mint-b")
    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        await add_membership(session, b.org.id, a.user.id, role="admin")
        await session.commit()

    for_a = await auth_cookie(a.user, org_id=a.org.id)
    for_b = await auth_cookie(a.user, org_id=b.org.id)
    orgless = await auth_cookie(a.user, org_id=None)

    async with client_for(b.host) as cb:
        # Genuinely a member of B — but only the session minted for B is a session on B.
        assert (await cb.get("/api/v1/companies", headers=for_b)).status_code == 200
        assert (await cb.get("/api/v1/companies", headers=for_a)).status_code == 401
        assert (await cb.get("/api/v1/companies", headers=orgless)).status_code == 401
