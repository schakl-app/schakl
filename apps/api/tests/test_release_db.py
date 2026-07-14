"""``ctx.release_db()`` — the pool-hygiene seam (docs/PERFORMANCE.md).

A request is one transaction on one pooled connection; an external call awaited inside it
pins that connection for the call's whole duration, which is how the pool melted down in
production. ``release_db()`` commits (returning the connection), then re-binds the RLS GUC
on a fresh transaction — and the re-bind is load-bearing: a bare commit leaves the next
transaction GUC-less and every RLS-forced table fails closed. These tests nail both halves,
plus the staged-mutation semantics ``acting_as``'s token refresh now relies on.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.models import OrgSettings
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from tests.conftest import make_tenant


def _settings_stmt(org_id):
    return select(OrgSettings).where(OrgSettings.org_id == org_id)


async def test_release_db_frees_the_connection_and_rebinds_rls() -> None:
    tenant = await make_tenant("releasedb")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        ctx = RequestContext(user=tenant.user, org=tenant.org, session=session)

        # GUC bound: the RLS-forced row is visible, and a transaction is open.
        row = await session.scalar(_settings_stmt(tenant.org.id))
        assert row is not None
        assert session.in_transaction()

        async with ctx.release_db():
            # Committed → no open transaction → the pooled connection is checked back in.
            assert not session.in_transaction()
            # Mutating an already-loaded object is memory only (what the Google token
            # refresh does mid-call); it must not need the connection.
            row.brand_name = "Rebound"

        # A fresh transaction with the GUC re-bound: RLS sees the tenant again, and the
        # mutation staged inside the block persists with this transaction's commit.
        assert (await session.scalar(_settings_stmt(tenant.org.id))) is not None
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        assert (await session.scalar(_settings_stmt(tenant.org.id))).brand_name == "Rebound"


async def test_rebinding_is_load_bearing_a_bare_commit_fails_closed() -> None:
    """The counterfactual: without the re-bind, the next transaction sees no tenant rows."""
    tenant = await make_tenant("releasedb2")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        assert (await session.scalar(_settings_stmt(tenant.org.id))) is not None

        await session.commit()  # release_db()'s entry, without its exit

        # New transaction, GUC unset → RLS fails closed. This is why the session is
        # off-limits inside a release_db() block.
        assert (await session.scalar(_settings_stmt(tenant.org.id))) is None
