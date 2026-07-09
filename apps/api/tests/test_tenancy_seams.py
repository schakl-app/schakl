"""The multi-org seams that must not rot (issue #26).

Three structural guarantees, asserted so a new module cannot silently opt out:

* every table is org-scoped **and** RLS-forced, unless it is on the explicit,
  reviewed instance-level allowlist (``app.db.INSTANCE_LEVEL_TABLES``);
* every cron job a module contributes binds tenant context through ``run_per_org``;
* hostname resolution rejects an unknown host outright — never "the only org".
"""

from __future__ import annotations

import importlib
import inspect

from sqlalchemy import text

from app.config import settings
from app.db import INSTANCE_LEVEL_TABLES, engine
from app.registry import registry
from tests.conftest import auth_cookie, make_tenant

# Not a mapped table; Alembic bookkeeping.
_NON_MODEL_TABLES = {"alembic_version"}


async def test_every_table_is_org_scoped_and_rls_forced() -> None:
    async with engine.connect() as conn:
        tables = {
            row.relname: row
            for row in (
                await conn.execute(
                    text(
                        """
                        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public' AND c.relkind = 'r'
                        """
                    )
                )
            ).all()
        }
        org_id_tables = {
            row.table_name
            for row in (
                await conn.execute(
                    text(
                        """
                        SELECT table_name FROM information_schema.columns
                        WHERE table_schema = 'public' AND column_name = 'org_id'
                        """
                    )
                )
            ).all()
        }
        policy_tables = {
            row.tablename
            for row in (
                await conn.execute(
                    text("SELECT tablename FROM pg_policies WHERE schemaname = 'public'")
                )
            ).all()
        }

    assert tables, "no tables found — is the schema migrated?"
    for name, row in sorted(tables.items()):
        if name in INSTANCE_LEVEL_TABLES or name in _NON_MODEL_TABLES:
            continue
        assert name in org_id_tables, f"table '{name}' has no org_id column (Golden Rule 1)"
        assert row.relrowsecurity, f"table '{name}' does not have RLS enabled"
        assert row.relforcerowsecurity, f"table '{name}' does not FORCE RLS (owner bypass)"
        assert name in policy_tables, f"table '{name}' has no RLS policy"


def test_every_module_cron_job_binds_tenant_context() -> None:
    for name in settings.enabled_modules:
        importlib.import_module(f"app.modules.{name}")
    jobs = [(module.name, job) for module in registry.all() for job in module.cron_jobs]
    for module_name, job in jobs:
        source = inspect.getsource(job.coroutine)
        assert "run_per_org" in source, (
            f"cron job '{job.coroutine.__name__}' of module '{module_name}' does not bind "
            "tenant context via run_per_org (CLAUDE.md §6)"
        )


async def test_unknown_hostname_is_rejected(client_for) -> None:
    tenant = await make_tenant("seam-known")
    headers = await auth_cookie(tenant.user)

    # The tenant's own host resolves…
    async with client_for(tenant.host) as client:
        assert (await client.get("/api/v1/meta/tenant")).status_code == 200

    # …an unknown slug does not, even though exactly one org exists (no fallback).
    async with client_for("nope.localhost") as client:
        response = await client.get("/api/v1/meta/tenant")
        assert response.status_code == 404
        assert response.json()["error"]["message"] == "errors.unknown_host"
        authed = await client.get("/api/v1/companies", headers=headers)
        assert authed.status_code == 404

    # The bare base domain encodes no slug either.
    async with client_for("localhost") as client:
        assert (await client.get("/api/v1/meta/tenant")).status_code == 404


async def test_only_a_verified_custom_domain_resolves(client_for) -> None:
    from app.core.models import Org
    from app.db import async_session_maker

    tenant = await make_tenant("seam-domain")
    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        org.custom_domain = "crm.acme.test"
        org.custom_domain_verified_at = None
        await session.commit()

    async with client_for("crm.acme.test") as client:
        assert (await client.get("/api/v1/meta/tenant")).status_code == 404

    from datetime import UTC, datetime

    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        org.custom_domain_verified_at = datetime.now(UTC)
        await session.commit()

    async with client_for("crm.acme.test") as client:
        response = await client.get("/api/v1/meta/tenant")
        assert response.status_code == 200
        assert response.json()["slug"] == "seam-domain"
