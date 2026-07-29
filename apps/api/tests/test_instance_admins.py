"""Delegated instance access (issue #26).

The feature exists so a second person can operate the platform without holding everything. So
the tests that matter are the ones about **what a delegated admin cannot do**: reach a route
whose capability they lack, grant themselves more, or leave the instance with no owner.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.core.instance import capabilities as caps
from app.core.models import InstanceAdmin
from app.db import async_session_maker
from tests.conftest import Tenant, auth_cookie, make_tenant


@pytest.fixture
def surface_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "instance_admin_enabled", True)


async def make_owner(tenant: Tenant) -> None:
    async with async_session_maker() as session:
        user = await session.get(User, tenant.user.id)
        user.is_superuser = True
        await session.commit()
    tenant.user.is_superuser = True


async def make_admin(tenant: Tenant, capabilities: list[str]) -> None:
    async with async_session_maker() as session:
        session.add(
            InstanceAdmin(
                user_id=tenant.user.id,
                capabilities=capabilities,
                granted_by_email="owner@admins.example",
            )
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# Reaching the surface at all
# --------------------------------------------------------------------------- #
async def test_a_plain_user_still_reaches_nothing(client_for, surface_on) -> None:
    """The pre-existing property, restated: widening the guard must not widen access."""
    tenant = await make_tenant("adm-none")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        assert (await client.get("/api/v1/instance/orgs", headers=headers)).status_code == 403


async def test_an_admin_reaches_only_what_it_holds(client_for, surface_on) -> None:
    tenant = await make_tenant("adm-scoped")
    await make_admin(tenant, [caps.ORGS_READ])
    headers = await auth_cookie(tenant.user)

    async with client_for(tenant.host) as client:
        # Holds orgs.read …
        assert (await client.get("/api/v1/instance/orgs", headers=headers)).status_code == 200
        # … and nothing else. Each of these declares a capability it was not granted.
        for method, path in (
            ("get", "/api/v1/instance/audit"),
            ("post", "/api/v1/instance/orgs"),
            ("get", f"/api/v1/instance/orgs/{uuid.uuid4()}/export"),
            ("post", f"/api/v1/instance/orgs/{uuid.uuid4()}/purge"),
            ("post", f"/api/v1/instance/orgs/{uuid.uuid4()}/impersonate"),
        ):
            response = await client.request(method.upper(), path, headers=headers, json={})
            assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


async def test_an_admin_with_an_emptied_grant_is_off_the_surface(
    client_for, surface_on
) -> None:
    """Revoking the last capability is revoking access, not leaving a shell that can still
    enumerate the console."""
    tenant = await make_tenant("adm-emptied")
    await make_admin(tenant, [])
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        assert (await client.get("/api/v1/instance/orgs", headers=headers)).status_code == 403


async def test_an_owner_holds_everything_without_a_row(client_for, surface_on) -> None:
    tenant = await make_tenant("adm-owner")
    await make_owner(tenant)
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        assert (await client.get("/api/v1/instance/orgs", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/instance/audit", headers=headers)).status_code == 200


async def test_a_stale_capability_key_is_not_honoured(client_for, surface_on) -> None:
    """A capability dropped from the catalog in a later release must stop working even while
    the string is still sitting in the row."""
    tenant = await make_tenant("adm-stale")
    await make_admin(tenant, ["instance.capability.that.no.longer.exists"])
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        assert (await client.get("/api/v1/instance/orgs", headers=headers)).status_code == 403


# --------------------------------------------------------------------------- #
# Delegation is not delegable
# --------------------------------------------------------------------------- #
async def test_an_admin_cannot_manage_admins(client_for, surface_on) -> None:
    """The anti-escalation invariant. An admin who could grant instance.impersonate to
    themselves would be an owner with extra steps."""
    tenant = await make_tenant("adm-noescalate")
    # Deliberately generous: everything in the catalog, and still not this.
    await make_admin(tenant, sorted(caps.CAPABILITY_KEYS))
    headers = await auth_cookie(tenant.user)

    async with client_for(tenant.host) as client:
        # 403, not 404: they are legitimately on this surface and simply may not do this.
        assert (await client.get("/api/v1/instance/admins", headers=headers)).status_code == 403
        granted = await client.post(
            "/api/v1/instance/admins",
            headers=headers,
            json={"email": "sneaky@admins.example", "capabilities": [caps.ORGS_PURGE]},
        )
        assert granted.status_code == 403
        promoted = await client.patch(
            f"/api/v1/instance/admins/{tenant.user.id}",
            headers=headers,
            json={"is_owner": True},
        )
        assert promoted.status_code == 403

    # And nothing was written.
    async with async_session_maker() as session:
        assert await session.scalar(
            select(User).where(User.email == "sneaky@admins.example")
        ) is None
        assert (await session.get(User, tenant.user.id)).is_superuser is False


# --------------------------------------------------------------------------- #
# Managing people, as an owner
# --------------------------------------------------------------------------- #
async def test_invite_creates_the_account_and_grants(client_for, surface_on) -> None:
    owner = await make_tenant("adm-invite")
    await make_owner(owner)
    headers = await auth_cookie(owner.user)

    async with client_for(owner.host) as client:
        created = await client.post(
            "/api/v1/instance/admins",
            headers=headers,
            json={
                "email": "Support@Admins.Example",
                "full_name": "Support Person",
                "capabilities": [caps.ORGS_READ, caps.AUDIT_READ],
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["email"] == "support@admins.example"  # normalised
        assert body["is_owner"] is False
        assert sorted(body["capabilities"]) == sorted([caps.ORGS_READ, caps.AUDIT_READ])

        # The catalog rides along, so the console renders checkboxes without hardcoding keys.
        listed = await client.get("/api/v1/instance/admins", headers=headers)
        assert listed.status_code == 200
        assert {c["key"] for c in listed.json()["catalog"]} == caps.CAPABILITY_KEYS
        assert any(p["is_owner"] for p in listed.json()["principals"])

    async with async_session_maker() as session:
        invited = await session.scalar(
            select(User).where(User.email == "support@admins.example")
        )
        # Created, but never as an owner: promoting is a second, explicit act, so a typo'd
        # invite cannot mint someone who holds everything.
        assert invited is not None and invited.is_superuser is False
        assert invited.is_active and not invited.is_verified


async def test_invite_with_nothing_ticked_grants_nothing(client_for, surface_on) -> None:
    """The safe default: a half-finished invite must never over-grant."""
    owner = await make_tenant("adm-empty-invite")
    await make_owner(owner)
    headers = await auth_cookie(owner.user)
    async with client_for(owner.host) as client:
        created = await client.post(
            "/api/v1/instance/admins", headers=headers, json={"email": "quiet@admins.example"}
        )
        assert created.status_code == 201
        assert created.json()["capabilities"] == []


async def test_unknown_capability_is_rejected_not_dropped(client_for, surface_on) -> None:
    """Silently ignoring one would hand back a 201 for a grant that did not happen."""
    owner = await make_tenant("adm-badcap")
    await make_owner(owner)
    headers = await auth_cookie(owner.user)
    async with client_for(owner.host) as client:
        response = await client.post(
            "/api/v1/instance/admins",
            headers=headers,
            json={"email": "typo@admins.example", "capabilities": ["instance.orgs.reed"]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["fields"] == {
            "capabilities": "errors.unknown_capability"
        }


async def test_promoting_to_owner_drops_the_capability_row(client_for, surface_on) -> None:
    """An owner holds everything implicitly; a row beside it would be a second, contradictory
    source of truth for the same person."""
    owner = await make_tenant("adm-promote-o")
    admin = await make_tenant("adm-promote-a")
    await make_owner(owner)
    await make_admin(admin, [caps.ORGS_READ])
    headers = await auth_cookie(owner.user)

    async with client_for(owner.host) as client:
        updated = await client.patch(
            f"/api/v1/instance/admins/{admin.user.id}", headers=headers, json={"is_owner": True}
        )
        assert updated.status_code == 200
        assert updated.json()["is_owner"] is True
        assert set(updated.json()["capabilities"]) == caps.CAPABILITY_KEYS

    async with async_session_maker() as session:
        assert await session.scalar(
            select(InstanceAdmin).where(InstanceAdmin.user_id == admin.user.id)
        ) is None


async def test_revoking_removes_every_form_of_access(client_for, surface_on) -> None:
    owner = await make_tenant("adm-revoke-o")
    admin = await make_tenant("adm-revoke-a")
    await make_owner(owner)
    await make_admin(admin, [caps.ORGS_READ])
    owner_headers = await auth_cookie(owner.user)
    admin_headers = await auth_cookie(admin.user)

    async with client_for(owner.host) as client:
        assert (
            await client.get("/api/v1/instance/orgs", headers=admin_headers)
        ).status_code == 200
        gone = await client.delete(
            f"/api/v1/instance/admins/{admin.user.id}", headers=owner_headers
        )
        assert gone.status_code == 204
        assert (
            await client.get("/api/v1/instance/orgs", headers=admin_headers)
        ).status_code == 403


# --------------------------------------------------------------------------- #
# Never lock the instance out
# --------------------------------------------------------------------------- #
async def test_the_last_owner_cannot_demote_themselves(client_for, surface_on) -> None:
    """A box nobody can administer is unrecoverable without database access — and a delegated
    admin cannot promote anyone, so there is no way back."""
    owner = await make_tenant("adm-last")
    await make_owner(owner)
    headers = await auth_cookie(owner.user)

    async with client_for(owner.host) as client:
        refused = await client.patch(
            f"/api/v1/instance/admins/{owner.user.id}", headers=headers, json={"is_owner": False}
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.last_instance_owner"

        also_refused = await client.delete(
            f"/api/v1/instance/admins/{owner.user.id}", headers=headers
        )
        assert also_refused.status_code == 409

    # The rollback is real, not just the response: they are still an owner.
    async with async_session_maker() as session:
        assert (await session.get(User, owner.user.id)).is_superuser is True


async def test_an_owner_may_step_down_once_a_second_exists(client_for, surface_on) -> None:
    first = await make_tenant("adm-two-a")
    second = await make_tenant("adm-two-b")
    await make_owner(first)
    await make_owner(second)
    headers = await auth_cookie(first.user)

    async with client_for(first.host) as client:
        stepped = await client.patch(
            f"/api/v1/instance/admins/{first.user.id}", headers=headers, json={"is_owner": False}
        )
        assert stepped.status_code == 200
        assert stepped.json()["is_owner"] is False

    async with async_session_maker() as session:
        assert (await session.get(User, first.user.id)).is_superuser is False
        assert (await session.get(User, second.user.id)).is_superuser is True


async def test_the_admins_surface_is_404_when_disabled(client_for, monkeypatch) -> None:
    monkeypatch.setattr(settings, "instance_admin_enabled", False)
    owner = await make_tenant("adm-off")
    await make_owner(owner)
    headers = await auth_cookie(owner.user)
    async with client_for(owner.host) as client:
        assert (await client.get("/api/v1/instance/admins", headers=headers)).status_code == 404
