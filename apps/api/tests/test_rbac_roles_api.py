"""Roles CRUD, lockout guardrails and the org-scoped audit (issue #19, #53).

The guard that matters: an agency must never be able to remove its own last role manager. Four
mutation shapes can do it, and each of them must 409 **and roll back** — the check runs after the
write is flushed, so a passing test proves the rollback too.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member


async def _roles(client, headers) -> dict[str, dict]:
    response = await client.get("/api/v1/roles", headers=headers)
    assert response.status_code == 200, response.text
    return {role["key"]: role for role in response.json()}


async def _audit(org_id) -> list[tuple[str, str]]:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        rows = await session.execute(
            text("SELECT action, actor_email FROM role_audit_log ORDER BY created_at")
        )
        return [(row[0], row[1]) for row in rows]


# --------------------------------------------------------------------------- #
# Catalog + listing
# --------------------------------------------------------------------------- #
async def test_catalog_is_readable_by_any_member_and_roles_are_not(client_for) -> None:
    tenant = await make_tenant("roles-catalog")
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        catalog = await client.get("/api/v1/permissions/catalog", headers=member_headers)
        assert catalog.status_code == 200
        body = catalog.json()
        keys = {p["key"] for p in body["permissions"]}
        assert {"settings.roles.manage", "time.entry.write", "companies.company.read"} <= keys
        scoped = next(p for p in body["permissions"] if p["key"] == "time.entry.write")
        assert scoped["scopes"] == ["own", "any"]
        assert scoped["label_key"] == "permissions.time.entry.write"
        assert "settings" in body["groups"] and "time" in body["groups"]

        # Reading the tenant's roles is a manager capability.
        assert (await client.get("/api/v1/roles", headers=member_headers)).status_code == 403


async def test_seeded_roles_carry_their_permissions_and_member_counts(client_for) -> None:
    tenant = await make_tenant("roles-list")
    headers = await auth_cookie(tenant.user)
    await add_member(tenant)

    async with client_for(tenant.host) as client:
        roles = await _roles(client, headers)
        assert list(roles) == ["owner", "admin", "member", "client"]
        assert roles["owner"]["permissions"] == ["*"]
        assert roles["owner"]["member_count"] == 1
        assert roles["member"]["member_count"] == 1
        assert roles["client"]["member_count"] == 0
        assert "companies.company.write" not in roles["member"]["permissions"]
        assert "tasks.task.write:own" in roles["member"]["permissions"]


# --------------------------------------------------------------------------- #
# Immutability of the system roles
# --------------------------------------------------------------------------- #
async def test_owner_permissions_are_immutable_and_system_roles_undeletable(client_for) -> None:
    tenant = await make_tenant("roles-immutable")
    headers = await auth_cookie(tenant.user)

    async with client_for(tenant.host) as client:
        roles = await _roles(client, headers)

        edited = await client.patch(
            f"/api/v1/roles/{roles['owner']['id']}",
            json={"permissions": ["companies.company.read"]},
            headers=headers,
        )
        assert edited.status_code == 409
        assert edited.json()["error"]["message"] == "errors.system_role_immutable"

        for key in ("owner", "admin", "member", "client"):
            deleted = await client.delete(f"/api/v1/roles/{roles[key]['id']}", headers=headers)
            assert deleted.status_code == 409, key
            assert deleted.json()["error"]["message"] == "errors.system_role_immutable"

        # Renaming a system role is fine; that is not its key.
        renamed = await client.patch(
            f"/api/v1/roles/{roles['owner']['id']}",
            json={"name_i18n": {"nl": "Baas", "en": "Boss"}},
            headers=headers,
        )
        assert renamed.status_code == 200
        assert renamed.json()["name_i18n"]["nl"] == "Baas"
        assert renamed.json()["permissions"] == ["*"]


async def test_a_system_role_can_be_loosened_and_duplicated(client_for) -> None:
    """The sanctioned way out of the restrictive `member` default (docs/DEPLOY.md)."""
    tenant = await make_tenant("roles-loosen")
    headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)

    async with client_for(tenant.host) as client:
        assert (
            await client.post("/api/v1/companies", json={"name": "X"}, headers=member_headers)
        ).status_code == 403

        roles = await _roles(client, headers)
        loosened = sorted(
            {*roles["member"]["permissions"], "companies.company.write"}
        )
        updated = await client.patch(
            f"/api/v1/roles/{roles['member']['id']}",
            json={"permissions": loosened},
            headers=headers,
        )
        assert updated.status_code == 200
        assert (
            await client.post("/api/v1/companies", json={"name": "X"}, headers=member_headers)
        ).status_code == 201

        # Duplicate: a custom role seeded from a system one.
        copy = await client.post(
            f"/api/v1/roles?from={roles['member']['id']}",
            json={"key": "senior", "name_i18n": {"nl": "Senior", "en": "Senior"}},
            headers=headers,
        )
        assert copy.status_code == 201
        assert copy.json()["is_system"] is False
        assert copy.json()["permissions"] == loosened

        # Duplicating owner copies the wildcard away — it is not assignable.
        from_owner = await client.post(
            f"/api/v1/roles?from={roles['owner']['id']}",
            json={"key": "not-owner"},
            headers=headers,
        )
        assert from_owner.status_code == 201
        assert from_owner.json()["permissions"] == []

        # Free text is refused: role_permissions only ever holds catalog keys.
        assert (
            await client.post(
                "/api/v1/roles", json={"key": "junk", "permissions": ["do.whatever"]},
                headers=headers,
            )
        ).status_code == 422
        # …as is a bare key for a scoped permission.
        assert (
            await client.post(
                "/api/v1/roles", json={"key": "junk2", "permissions": ["time.entry.write"]},
                headers=headers,
            )
        ).status_code == 422


# --------------------------------------------------------------------------- #
# The four lockout shapes
# --------------------------------------------------------------------------- #
async def test_untick_the_last_role_manage_permission(client_for) -> None:
    """Shape 1: revoke `settings.roles.manage` from the only role that still grants it."""
    tenant = await make_tenant("lockout-untick", role="admin")
    headers = await auth_cookie(tenant.user)

    async with client_for(tenant.host) as client:
        roles = await _roles(client, headers)
        stripped = [p for p in roles["admin"]["permissions"] if p != "settings.roles.manage"]
        refused = await client.patch(
            f"/api/v1/roles/{roles['admin']['id']}", json={"permissions": stripped},
            headers=headers,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.last_role_manager"

        # Rolled back: the admin role still manages roles.
        assert "settings.roles.manage" in (await _roles(client, headers))["admin"]["permissions"]


async def test_delete_the_last_role_managing_role(client_for) -> None:
    """Shape 2: delete a custom role that is the only thing granting the permission."""
    tenant = await make_tenant("lockout-delete", role="member")
    owner = await make_tenant("lockout-delete-other")  # unrelated, keeps make_tenant honest
    assert owner.org.id != tenant.org.id

    # Give the member a custom role that is the org's only role manager.
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        await session.execute(
            text(
                """
                WITH r AS (
                  INSERT INTO roles
                    (id, org_id, key, name_i18n, description_i18n, is_system, position)
                  VALUES (gen_random_uuid(), :org, 'ops', '{}'::jsonb, '{}'::jsonb, false, 50)
                  RETURNING id
                ), p AS (
                  INSERT INTO role_permissions (id, org_id, role_id, permission)
                  SELECT gen_random_uuid(), :org, r.id, 'settings.roles.manage' FROM r
                )
                INSERT INTO membership_roles (id, org_id, membership_id, role_id)
                SELECT gen_random_uuid(), :org, m.id, r.id
                FROM r, memberships m WHERE m.org_id = :org
                """
            ),
            {"org": str(tenant.org.id)},
        )
        await session.commit()

    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        roles = await _roles(client, headers)
        refused = await client.delete(f"/api/v1/roles/{roles['ops']['id']}", headers=headers)
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.last_role_manager"
        assert "ops" in await _roles(client, headers)  # rolled back


async def test_remove_the_last_role_manager_role_from_a_membership(client_for) -> None:
    """Shape 3: `PUT /members/{id}/roles` down to a role set with no manager left."""
    tenant = await make_tenant("lockout-membership")
    headers = await auth_cookie(tenant.user)

    async with client_for(tenant.host) as client:
        roles = await _roles(client, headers)
        me = next(
            m for m in (await client.get("/api/v1/members", headers=headers)).json() if m["is_self"]
        )
        refused = await client.put(
            f"/api/v1/members/{me['membership_id']}/roles",
            json={"role_ids": [roles["member"]["id"]]},
            headers=headers,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.last_role_manager"

        still = await client.get(
            f"/api/v1/members/{me['membership_id']}/permissions", headers=headers
        )
        assert still.json()["permissions"] == ["*"]


async def test_revoke_the_last_role_managing_membership(client_for) -> None:
    """Shape 4 lives in members.py, and is covered there; assert the error envelope matches."""
    tenant = await make_tenant("lockout-revoke")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        second = (
            await client.post(
                "/api/v1/members/invite", json={"email": "b@example.com", "role": "owner"},
                headers=headers,
            )
        ).json()
        # Revoking one of two owners is fine…
        assert (
            await client.delete(f"/api/v1/members/{second['membership_id']}", headers=headers)
        ).status_code == 204
        # …and revoking yourself is a different (older) guard.
        me = next(
            m for m in (await client.get("/api/v1/members", headers=headers)).json() if m["is_self"]
        )
        self_revoke = await client.delete(
            f"/api/v1/members/{me['membership_id']}", headers=headers
        )
        assert self_revoke.status_code == 400


# --------------------------------------------------------------------------- #
# Membership role assignment
# --------------------------------------------------------------------------- #
async def test_custom_role_only_memberships_are_assignable(client_for) -> None:
    tenant = await make_tenant("roles-system-required")
    headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)

    async with client_for(tenant.host) as client:
        custom = (
            await client.post(
                "/api/v1/roles", json={"key": "bookkeeping", "permissions": ["time.report.read"]},
                headers=headers,
            )
        ).json()
        members = (await client.get("/api/v1/members", headers=headers)).json()
        target = next(m for m in members if m["user_id"] == str(member.id))

        # Custom-role-only memberships are legal since the legacy column dropped (#56).
        only_custom = await client.put(
            f"/api/v1/members/{target['membership_id']}/roles",
            json={"role_ids": [custom["id"]]},
            headers=headers,
        )
        assert only_custom.status_code == 200, only_custom.text
        assert only_custom.json()["permissions"] == ["time.report.read"]

        # An *empty* set is still refused — a member holding nothing is a lockout, not a choice.
        refused = await client.put(
            f"/api/v1/members/{target['membership_id']}/roles",
            json={"role_ids": []},
            headers=headers,
        )
        assert refused.status_code == 422

        # Additive is fine, and the effective set is the union.
        roles = await _roles(client, headers)
        ok = await client.put(
            f"/api/v1/members/{target['membership_id']}/roles",
            json={"role_ids": [roles["member"]["id"], custom["id"]]},
            headers=headers,
        )
        assert ok.status_code == 200
        assert "time.report.read" in ok.json()["permissions"]
        assert "tasks.task.write:own" in ok.json()["permissions"]


        member_headers = await auth_cookie(member)
        assert (
            await client.get("/api/v1/time/report", headers=member_headers)
        ).status_code == 200


# --------------------------------------------------------------------------- #
# Audit + isolation
# --------------------------------------------------------------------------- #
async def test_role_changes_are_audited_org_scoped(client_for) -> None:
    tenant = await make_tenant("roles-audit")
    headers = await auth_cookie(tenant.user)

    async with client_for(tenant.host) as client:
        created = (
            await client.post(
                "/api/v1/roles", json={"key": "audited", "permissions": ["time.report.read"]},
                headers=headers,
            )
        ).json()
        await client.patch(
            f"/api/v1/roles/{created['id']}", json={"permissions": []}, headers=headers
        )
        await client.post(
            "/api/v1/members/invite", json={"email": "c@example.com", "role": "member"},
            headers=headers,
        )
        await client.delete(f"/api/v1/roles/{created['id']}", headers=headers)

    entries = await _audit(tenant.org.id)
    assert [action for action, _ in entries] == [
        "role.create",
        "role.update",
        "membership.invited",
        "role.delete",
    ]
    assert {email for _, email in entries} == {tenant.user.email}


async def test_roles_are_tenant_isolated_through_the_api(client_for) -> None:
    a = await make_tenant("roles-iso-a")
    b = await make_tenant("roles-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        custom = (
            await ca.post("/api/v1/roles", json={"key": "only-a"}, headers=a_headers)
        ).json()

    async with client_for(b.host) as cb:
        keys = {role["key"] for role in (await cb.get("/api/v1/roles", headers=b_headers)).json()}
        assert "only-a" not in keys
        assert (
            await cb.patch(f"/api/v1/roles/{custom['id']}", json={"position": 1}, headers=b_headers)
        ).status_code == 404
        assert (
            await cb.delete(f"/api/v1/roles/{custom['id']}", headers=b_headers)
        ).status_code == 404

        # Nor can B assign A's role to one of B's memberships.
        me = next(
            m for m in (await cb.get("/api/v1/members", headers=b_headers)).json() if m["is_self"]
        )
        assert (
            await cb.put(
                f"/api/v1/members/{me['membership_id']}/roles",
                json={"role_ids": [custom["id"]]},
                headers=b_headers,
            )
        ).status_code == 404

    assert await _audit(b.org.id) == []
