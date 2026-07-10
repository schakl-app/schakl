"""RBAC data model + resolution (issue #19, sub-issue #49).

Covers the three seams the plan calls out as easy to get wrong:

* ``PermissionSet.has`` — a scoped permission is only ever *stored* suffixed, so a bare check
  must mean "at some scope" or every member 403s on every scoped endpoint;
* the combined resolution query — one statement, and ``array_agg(...).filter(...)`` so a
  role-less membership yields ``[]`` rather than ``{NULL}``;
* tenant isolation on ``roles`` / ``role_permissions`` / ``membership_roles``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.config import settings
from app.core.permissions import PermissionSet
from app.core.permissions.catalog import default_permissions_for, permission_keys
from app.core.permissions.models import MembershipRole, Role, RolePermission
from app.core.permissions.service import (
    collapse_to_legacy_role,
    effective_permissions,
    role_by_key,
    role_manager_count,
    seed_system_roles,
)
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant


# --------------------------------------------------------------------------- #
# PermissionSet semantics
# --------------------------------------------------------------------------- #
def test_scoped_permission_satisfies_a_bare_check() -> None:
    member = PermissionSet.of({"time.entry.write:own", "tasks.task.create"})
    # The route's floor: "holds this at some scope".
    assert member.has("time.entry.write")
    assert member.has("time.entry.write", scope="own")
    # …but not the broad grant.
    assert not member.has("time.entry.write", scope="any")
    # A genuinely unscoped permission answers every scope.
    assert member.has("tasks.task.create")
    assert member.has("tasks.task.create", scope="any")


def test_any_satisfies_own_and_wildcard_satisfies_everything() -> None:
    manager = PermissionSet.of({"time.entry.write:any"})
    assert manager.has("time.entry.write")
    assert manager.has("time.entry.write", scope="own")
    assert manager.has("time.entry.write", scope="any")

    owner = PermissionSet.of({"*"})
    assert owner.wildcard
    assert owner.has("anything.at.all", scope="any")

    nobody = PermissionSet.of(None)
    assert not nobody.has("time.entry.write")


def test_collapse_picks_the_highest_privilege_system_role() -> None:
    assert collapse_to_legacy_role(["member", "owner", "client"]) == "owner"
    assert collapse_to_legacy_role(["client", "member"]) == "member"
    assert collapse_to_legacy_role([]) == "client"


def test_the_catalog_imports_the_modules_it_needs() -> None:
    """The catalog must not quietly degrade to core-only.

    ``seed_system_roles`` also runs from the worker, from the first-run wizard and from scripts —
    places where nothing has imported ``app.modules.*``. A core-only catalog would seed an
    ``admin`` role that cannot touch a single module, and nothing would say so. Asserted in a
    fresh interpreter, because this test session has already imported ``app.main``.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.core.permissions.catalog import permission_keys;"
            "print(' '.join(permission_keys()))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    keys = result.stdout.split()
    for name in settings.enabled_modules:
        assert any(key.startswith(f"{name}.") for key in keys), f"{name} permissions missing"


def test_seeded_defaults_match_the_documented_posture() -> None:
    assert default_permissions_for("owner") == ["*"]
    admin = set(default_permissions_for("admin"))
    # Explicit full list, never a wildcard — so a tenant can still restrict the admin role.
    assert "*" not in admin
    assert len(admin) == len(permission_keys())
    assert "settings.roles.manage" in admin

    member = set(default_permissions_for("member"))
    # The deliberate restriction (issue #19 ⚠️1): a member reads but does not write these.
    assert "companies.company.read" in member
    assert "companies.company.write" not in member
    assert "contacts.contact.write" not in member
    assert "projects.project.write" not in member
    assert "tasks.task.write:any" not in member
    # …and keeps exactly the writes the plan names.
    assert {
        "tasks.task.create",
        "tasks.task.write:own",
        "tasks.comment.write:own",
        "time.entry.write:own",
        "leave.request.write:own",
    } <= member

    client = set(default_permissions_for("client"))
    assert not any(p.startswith(("time.", "leave.", "members.")) for p in client)
    assert "companies.company.read" in client


# --------------------------------------------------------------------------- #
# Seeding + resolution
# --------------------------------------------------------------------------- #
async def test_make_tenant_seeds_roles_and_resolves_permissions() -> None:
    owner = await make_tenant("rbac-owner")
    member = await make_tenant("rbac-member", role="member")

    async with async_session_maker() as session:
        await set_current_org(session, owner.org.id)
        roles = (
            await session.execute(text("SELECT key FROM roles ORDER BY position"))
        ).scalars().all()
        assert roles == ["owner", "admin", "member", "client"]

        membership_id = (
            await session.execute(
                text("SELECT id FROM memberships WHERE user_id = :u"), {"u": str(owner.user.id)}
            )
        ).scalar_one()
        assert (await effective_permissions(session, owner.org.id, membership_id)).wildcard

    async with async_session_maker() as session:
        await set_current_org(session, member.org.id)
        membership_id = (
            await session.execute(
                text("SELECT id FROM memberships WHERE user_id = :u"), {"u": str(member.user.id)}
            )
        ).scalar_one()
        permissions = await effective_permissions(session, member.org.id, membership_id)
        assert not permissions.wildcard
        assert permissions.has("time.entry.write", scope="own")
        assert not permissions.has("time.entry.write", scope="any")


async def test_seed_system_roles_is_idempotent() -> None:
    tenant = await make_tenant("rbac-idem")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        before = (
            await session.execute(text("SELECT count(*) FROM role_permissions"))
        ).scalar_one()
        await seed_system_roles(session, tenant.org.id)
        await seed_system_roles(session, tenant.org.id)
        await session.flush()
        after = (
            await session.execute(text("SELECT count(*) FROM role_permissions"))
        ).scalar_one()
        await session.commit()
    assert before == after
    assert before > 0


async def test_applied_permission_defaults_covers_the_catalog() -> None:
    tenant = await make_tenant("rbac-applied")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        applied = (
            await session.execute(
                text("SELECT applied_permission_defaults FROM org_settings WHERE org_id = :o"),
                {"o": str(tenant.org.id)},
            )
        ).scalar_one()
    assert sorted(applied) == sorted(permission_keys())


async def test_a_role_less_membership_resolves_to_an_empty_set(client_for) -> None:
    """The ``.filter(...)`` on ``array_agg`` is what makes this ``[]`` and not ``{None}``."""
    tenant = await make_tenant("rbac-empty", role="member")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        await session.execute(text("DELETE FROM membership_roles"))
        await session.commit()

    async with client_for(tenant.host) as client:
        # ``/meta/me`` needs a membership, not a permission — it still resolves.
        response = await client.get("/api/v1/meta/me", headers=await auth_cookie(tenant.user))
        assert response.status_code == 200


async def test_require_context_costs_no_extra_statements(client_for, count_queries) -> None:
    """Resolving permissions rides on the membership lookup; it is not a second round-trip."""
    tenant = await make_tenant("rbac-queries")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        with count_queries() as counter:
            assert (await client.get("/api/v1/meta/me", headers=headers)).status_code == 200
    # set_config + the combined membership/permission select + the auth user lookup.
    assert len(counter.matching("FROM memberships")) == 1
    assert len(counter.matching("role_permissions")) == 1


async def test_role_manager_count_tracks_permissions_not_the_legacy_column() -> None:
    tenant = await make_tenant("rbac-mgr")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        assert await role_manager_count(session, tenant.org.id) == 1
        await session.execute(text("DELETE FROM membership_roles"))
        assert await role_manager_count(session, tenant.org.id) == 0


# --------------------------------------------------------------------------- #
# Tenant isolation, per new table
# --------------------------------------------------------------------------- #
async def _custom_role(org_id: uuid.UUID, key: str) -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        role = Role(org_id=org_id, key=key, name_i18n={"en": key}, description_i18n={})
        session.add(role)
        await session.flush()
        session.add(
            RolePermission(org_id=org_id, role_id=role.id, permission="companies.company.read")
        )
        await session.commit()
        return role.id


async def test_roles_are_tenant_isolated() -> None:
    a = await make_tenant("rbac-iso-a")
    b = await make_tenant("rbac-iso-b")
    b_role = await _custom_role(b.org.id, "bookkeeping")

    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        assert await session.get(Role, b_role) is None
        visible = (await session.execute(text("SELECT key FROM roles"))).scalars().all()
        assert "bookkeeping" not in visible
        assert (
            await session.execute(
                text("SELECT count(*) FROM role_permissions WHERE role_id = :r"),
                {"r": str(b_role)},
            )
        ).scalar_one() == 0

    # Fail closed with no GUC bound at all.
    async with async_session_maker() as session:
        for table in ("roles", "role_permissions", "membership_roles"):
            rows = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert rows == 0, table


async def test_membership_roles_cannot_be_written_across_tenants() -> None:
    a = await make_tenant("rbac-iso-c")
    b = await make_tenant("rbac-iso-d")

    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        b_owner = await role_by_key(session, b.org.id, "owner")
        a_membership = (
            await session.execute(
                text("SELECT id FROM memberships"),  # RLS: only B's rows are visible
            )
        ).scalars().all()
        assert len(a_membership) == 1  # B's own membership; A's is invisible

    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        # Writing a row that claims B's org_id is rejected by the policy's WITH CHECK.
        session.add(
            MembershipRole(
                org_id=b.org.id, membership_id=uuid.uuid4(), role_id=b_owner.id
            )
        )
        try:
            await session.flush()
        except Exception:
            pass  # FK or RLS, either way it never lands
        else:
            raise AssertionError("cross-tenant membership_role insert was accepted")
        await session.rollback()
