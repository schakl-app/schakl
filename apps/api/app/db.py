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
INSTANCE_LEVEL_TABLES = frozenset({"orgs", "users", "instance_audit_log"})


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
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
