"""Database layer: declarative ``Base``, async engine/session, and the RLS binding.

Tenant isolation (Golden Rule 1) is enforced two ways that must always agree:
  1. the application filters every domain query by ``org_id`` (see ``core/tenancy.py``); and
  2. Postgres Row-Level Security, bound per request via the ``app.current_org`` GUC set here.

``set_config('app.current_org', <org>, true)`` is *transaction-local*, so a tenant request
runs inside a single transaction: we set the GUC, do the work, then commit once at the end.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Stable, predictable constraint names → clean Alembic autogenerate diffs.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

RLS_GUC = "app.current_org"

#: Tables that are deliberately instance-level rather than org-scoped: the tenant table
#: itself, global identity, and the cross-tenant audit trail (issue #26). Everything else
#: must carry ``org_id`` + an RLS policy — tests/test_tenancy_seams.py enforces exactly
#: this list, so extending it is a reviewed decision, not an accident.
INSTANCE_LEVEL_TABLES = frozenset(
    {
        "orgs",
        "users",
        "instance_audit_log",
        "instance_license",  # one product license per installation (issue #137)
        # Cloud (epic #199): the instance owner's provisioning credentials, and the org-issued
        # service-access grants the owner must present before touching tenant data. Both are
        # read by the instance surface *before* any tenant is bound, so they cannot sit under
        # RLS; writes are org-bound by code and audited.
        "instance_api_keys",
        "service_access_grants",
    }
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    # Never the SQLAlchemy defaults (5+10, 30 s wait): with one transaction per request a
    # burst of slow requests drained that pool and every other request queued 30 s on
    # checkout before 500ing — a sitewide freeze. Sized for the SSR fan-out, failing fast
    # when saturated; see config.py and docs/PERFORMANCE.md.
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_pool_max_overflow,
    pool_timeout=settings.db_pool_timeout_seconds,
)

async_session_maker = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def set_current_org(session: AsyncSession, org_id: uuid.UUID | None) -> None:
    """Bind the connection's RLS GUC to ``org_id`` for the current transaction.

    Passing ``None`` clears it → RLS policies fail closed (no rows), which is the
    safe default for any code path that has not resolved a tenant.
    """
    await session.execute(
        text(f"SELECT set_config('{RLS_GUC}', :val, true)"),
        {"val": str(org_id) if org_id is not None else ""},
    )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Plain (non-tenant) session for global tables (users, orgs) and auth flows.

    Domain requests use ``core.tenancy.require_context`` instead, which additionally
    binds the RLS GUC. This dependency never sets an org, so RLS-forced domain tables
    are invisible through it by design.
    """
    async with async_session_maker() as session:
        yield session
