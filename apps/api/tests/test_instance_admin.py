"""Instance administration (issue #26): setup wizard, org lifecycle, impersonation,
domain verification, export/import — including the gates that keep it all shut by default."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.core.models import InstanceAuditLog, Org
from app.db import async_session_maker
from tests.conftest import Tenant, auth_cookie, make_tenant


@pytest.fixture
def instance_admin_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "instance_admin_enabled", True)


async def make_instance_owner(tenant: Tenant) -> None:
    async with async_session_maker() as session:
        user = await session.get(User, tenant.user.id)
        user.is_superuser = True
        await session.commit()
    tenant.user.is_superuser = True  # keep the detached copy honest for auth_cookie


async def audit_actions() -> list[str]:
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(InstanceAuditLog.action).order_by(InstanceAuditLog.created_at.asc())
            )
        ).scalars()
        return list(rows)


# --------------------------------------------------------------------------- #
# First-run setup
# --------------------------------------------------------------------------- #
_SETUP_BODY = {
    "org_name": "Acme Agency",
    "slug": "acme",
    "brand_name": "Acme",
    "locale": "nl",
    "owner_email": "owner@example.com",
    "owner_password": "supersecret1",
    "owner_full_name": "Eigenaar",
}


async def test_setup_flow_claims_host_and_creates_instance_owner(client_for) -> None:
    async with client_for("hq.acme.test") as client:
        status = await client.get("/api/v1/setup/status")
        assert status.json() == {"needs_setup": True}

        created = await client.post("/api/v1/setup", json=_SETUP_BODY)
        assert created.status_code == 201
        assert created.json() == {"slug": "acme", "host": "hq.acme.test"}

        # The surface closes the moment an org exists.
        assert (await client.get("/api/v1/setup/status")).json() == {"needs_setup": False}
        again = await client.post("/api/v1/setup", json=_SETUP_BODY | {"slug": "other"})
        assert again.status_code == 409
        assert again.json()["error"]["message"] == "errors.setup_already_done"

        # The wizard's host resolves (claimed as a verified custom domain)…
        branding = await client.get("/api/v1/meta/tenant")
        assert branding.status_code == 200
        assert branding.json()["slug"] == "acme"
        assert branding.json()["brand_name"] == "Acme"

        # …and the owner can log in there, as org owner *and* instance owner.
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": "owner@example.com", "password": "supersecret1"},
        )
        assert login.status_code in (200, 204)

    async with async_session_maker() as session:
        owner = await session.scalar(select(User).where(User.email == "owner@example.com"))
        assert owner is not None and owner.is_superuser and owner.is_verified
    assert "setup" in await audit_actions()


async def test_setup_on_slug_host_claims_no_domain(client_for) -> None:
    async with client_for("acme.localhost") as client:
        created = await client.post("/api/v1/setup", json=_SETUP_BODY)
        assert created.status_code == 201
        assert created.json()["host"] is None
        assert (await client.get("/api/v1/meta/tenant")).json()["slug"] == "acme"


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
async def test_instance_admin_is_disabled_by_default(client_for) -> None:
    admin = await make_tenant("gate-admin")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)
    async with client_for(admin.host) as client:
        response = await client.get("/api/v1/instance/orgs", headers=headers)
        assert response.status_code == 404  # surface hidden, not merely forbidden


async def test_instance_admin_requires_instance_owner(
    client_for, instance_admin_enabled
) -> None:
    org_owner = await make_tenant("gate-owner")  # org owner, NOT instance owner
    headers = await auth_cookie(org_owner.user)
    async with client_for(org_owner.host) as client:
        response = await client.get("/api/v1/instance/orgs", headers=headers)
        assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Org lifecycle
# --------------------------------------------------------------------------- #
async def test_org_lifecycle(client_for, instance_admin_enabled) -> None:
    admin = await make_tenant("life-admin")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)

    async with client_for(admin.host) as client:
        # Create, with an invited owner.
        created = await client.post(
            "/api/v1/instance/orgs",
            json={"name": "Client Co", "slug": "client-co", "owner_email": "boss@example.com"},
            headers=headers,
        )
        assert created.status_code == 201
        org_id = created.json()["id"]
        assert created.json()["status"] == "active"

        dup = await client.post(
            "/api/v1/instance/orgs",
            json={"name": "Dup", "slug": "client-co"},
            headers=headers,
        )
        assert dup.status_code == 409
        assert dup.json()["error"]["message"] == "errors.slug_taken"

        reserved = await client.post(
            "/api/v1/instance/orgs", json={"name": "App", "slug": "app"}, headers=headers
        )
        assert reserved.status_code == 422

        detail = await client.get(f"/api/v1/instance/orgs/{org_id}", headers=headers)
        assert detail.status_code == 200
        assert [m["email"] for m in detail.json()["members"]] == ["boss@example.com"]
        assert detail.json()["enabled_modules"]  # defaults applied

        # Rename + re-slug.
        renamed = await client.patch(
            f"/api/v1/instance/orgs/{org_id}",
            json={"name": "Client Corp", "slug": "client-corp"},
            headers=headers,
        )
        assert renamed.status_code == 200
        assert (renamed.json()["name"], renamed.json()["slug"]) == ("Client Corp", "client-corp")

        # Suspend: branding stays up (flagged), authenticated requests are blocked.
        assert (
            await client.post(f"/api/v1/instance/orgs/{org_id}/suspend", headers=headers)
        ).json()["status"] == "suspended"

    async with client_for("client-corp.localhost") as client:
        branding = await client.get("/api/v1/meta/tenant")
        assert branding.status_code == 200 and branding.json()["suspended"] is True
        blocked = await client.get("/api/v1/companies", headers=await auth_cookie(admin.user))
        assert blocked.status_code == 403
        assert blocked.json()["error"]["message"] == "errors.org_suspended"

    async with client_for(admin.host) as client:
        assert (
            await client.post(f"/api/v1/instance/orgs/{org_id}/activate", headers=headers)
        ).json()["status"] == "active"

        # Soft delete → org stops resolving entirely.
        deleted = await client.delete(f"/api/v1/instance/orgs/{org_id}", headers=headers)
        assert deleted.status_code == 200 and deleted.json()["status"] == "deleted"

    async with client_for("client-corp.localhost") as client:
        assert (await client.get("/api/v1/meta/tenant")).status_code == 404

    async with client_for(admin.host) as client:
        # Purge refuses without a post-delete export…
        no_export = await client.post(
            f"/api/v1/instance/orgs/{org_id}/purge",
            json={"confirm": "client-corp"},
            headers=headers,
        )
        assert no_export.status_code == 409
        assert no_export.json()["error"]["message"] == "errors.export_required"

        exported = await client.get(f"/api/v1/instance/orgs/{org_id}/export", headers=headers)
        assert exported.status_code == 200

        # …and with the wrong confirmation.
        wrong = await client.post(
            f"/api/v1/instance/orgs/{org_id}/purge",
            json={"confirm": "nope"},
            headers=headers,
        )
        assert wrong.status_code == 422

        purged = await client.post(
            f"/api/v1/instance/orgs/{org_id}/purge",
            json={"confirm": "client-corp"},
            headers=headers,
        )
        assert purged.status_code == 204

        listing = await client.get("/api/v1/instance/orgs", headers=headers)
        assert [o["slug"] for o in listing.json()] == ["life-admin"]

    # The invited user (global identity) survives the purge; the audit trail names the org.
    async with async_session_maker() as session:
        assert await session.scalar(
            select(User).where(User.email == "boss@example.com")
        ) is not None
        assert await session.scalar(select(Org).where(Org.slug == "client-corp")) is None
    actions = await audit_actions()
    for expected in (
        "org.create",
        "org.update",
        "org.suspended",
        "org.activate",
        "org.deleted",
        "org.export",
        "org.purge",
    ):
        assert expected in actions, f"missing audit action {expected}: {actions}"


async def test_org_modules_update(client_for, instance_admin_enabled) -> None:
    admin = await make_tenant("mod-admin")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)
    other = await make_tenant("mod-other")

    async with client_for(admin.host) as client:
        bad = await client.patch(
            f"/api/v1/instance/orgs/{other.org.id}/modules",
            json={"enabled_modules": ["tasks"]},  # missing the companies hub
            headers=headers,
        )
        assert bad.status_code == 422

        ok = await client.patch(
            f"/api/v1/instance/orgs/{other.org.id}/modules",
            json={"enabled_modules": ["companies", "tasks"]},
            headers=headers,
        )
        assert ok.status_code == 200
        assert ok.json()["enabled_modules"] == ["companies", "tasks"]


# --------------------------------------------------------------------------- #
# Impersonation
# --------------------------------------------------------------------------- #
async def test_impersonation_is_time_boxed_audited_and_visible(
    client_for, instance_admin_enabled
) -> None:
    admin = await make_tenant("imp-admin")
    await make_instance_owner(admin)
    admin_headers = await auth_cookie(admin.user)
    target = await make_tenant("imp-target", email="member@example.org", role="member")

    async with client_for(admin.host) as client:
        grant = await client.post(
            f"/api/v1/instance/orgs/{target.org.id}/impersonate",
            json={"user_id": str(target.user.id), "minutes": 9999},
            headers=admin_headers,
        )
        assert grant.status_code == 200
        body = grant.json()
        token = body["token"]

    # Clamped to the configured maximum (60 min).
    from datetime import UTC, datetime, timedelta

    expires_at = datetime.fromisoformat(body["expires_at"])
    max_allowed = timedelta(minutes=settings.impersonation_max_minutes + 1)
    assert expires_at <= datetime.now(UTC) + max_allowed

    both_cookies = {"Cookie": f"{admin_headers['Cookie']}; schakl_impersonate={token}"}

    # On the target org's host the admin now *is* the member — visibly.
    async with client_for(target.host) as client:
        me = await client.get("/api/v1/meta/me", headers=both_cookies)
        assert me.status_code == 200
        assert me.json()["email"] == "member@example.org"
        assert me.json()["role"] == "member"
        assert me.json()["impersonated_by"] == admin.user.email
        assert me.json()["impersonation_expires_at"] is not None
        assert me.json()["is_instance_admin"] is False  # effective user, not the admin

    # Without the grant cookie the admin is still not a member of the target org.
    async with client_for(target.host) as client:
        assert (await client.get("/api/v1/meta/me", headers=admin_headers)).status_code == 403

    # A non-superuser session cannot activate the grant.
    other = await make_tenant("imp-bystander")
    other_cookie = await auth_cookie(other.user)
    hijack = {"Cookie": f"{other_cookie['Cookie']}; schakl_impersonate={token}"}
    async with client_for(target.host) as client:
        assert (await client.get("/api/v1/meta/me", headers=hijack)).status_code == 403

    # Disabling the flag kills outstanding grants instantly.
    settings.instance_admin_enabled = False
    try:
        async with client_for(target.host) as client:
            assert (await client.get("/api/v1/meta/me", headers=both_cookies)).status_code == 403
    finally:
        settings.instance_admin_enabled = True

    # Stop is audited.
    async with client_for(admin.host) as client:
        stopped = await client.post("/api/v1/instance/impersonation/stop", headers=admin_headers)
        assert stopped.status_code == 204
    actions = await audit_actions()
    assert "impersonate.start" in actions and "impersonate.stop" in actions


# --------------------------------------------------------------------------- #
# Export / import
# --------------------------------------------------------------------------- #
async def test_export_import_roundtrip(client_for, instance_admin_enabled) -> None:
    admin = await make_tenant("port-admin")
    await make_instance_owner(admin)
    admin_headers = await auth_cookie(admin.user)

    source = await make_tenant("port-src", email="portsrc-owner@example.com")
    source_headers = await auth_cookie(source.user)
    async with client_for(source.host) as client:
        company = await client.post(
            "/api/v1/companies", json={"name": "Rondreis BV"}, headers=source_headers
        )
        assert company.status_code == 201
        company_id = company.json()["id"]
        contact = await client.post(
            "/api/v1/contacts",
            json={"first_name": "Piet", "last_name": "Prik", "company_ids": [company_id]},
            headers=source_headers,
        )
        assert contact.status_code == 201

    async with client_for(admin.host) as client:
        exported = await client.get(
            f"/api/v1/instance/orgs/{source.org.id}/export", headers=admin_headers
        )
        assert exported.status_code == 200
        payload = exported.json()
        assert payload["format"] == 1
        assert len(payload["tables"]["companies"]) == 1
        assert any(u["email"] == "portsrc-owner@example.com" for u in payload["users"])

        imported = await client.post(
            "/api/v1/instance/orgs/import",
            json={"slug": "port-copy", "data": payload},
            headers=admin_headers,
        )
        assert imported.status_code == 201
        body = imported.json()
        assert body["org"]["slug"] == "port-copy"
        assert body["tables"]["companies"] == 1
        assert body["tables"]["contacts"] == 1
        assert body["org"]["custom_domain"] is None

        # Importing the same slug twice conflicts.
        dup = await client.post(
            "/api/v1/instance/orgs/import",
            json={"slug": "port-copy", "data": payload},
            headers=admin_headers,
        )
        assert dup.status_code == 409

    # The exported owner (matched by email) can use the imported org; FKs were remapped.
    async with client_for("port-copy.localhost") as client:
        companies = await client.get("/api/v1/companies", headers=source_headers)
        assert companies.status_code == 200
        assert companies.json()["total"] == 1
        copied = companies.json()["items"][0]
        assert copied["name"] == "Rondreis BV"
        assert copied["id"] != company_id  # fresh primary keys

        contacts = await client.get(
            "/api/v1/contacts", params={"company_id": copied["id"]}, headers=source_headers
        )
        assert contacts.status_code == 200
        assert contacts.json()["total"] == 1

    # …and the source org is untouched.
    async with client_for(source.host) as client:
        assert (await client.get("/api/v1/companies", headers=source_headers)).json()[
            "total"
        ] == 1


async def test_import_rejects_schema_mismatch(client_for, instance_admin_enabled) -> None:
    admin = await make_tenant("mismatch-admin")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)
    source = await make_tenant("mismatch-src")

    async with client_for(admin.host) as client:
        payload = (
            await client.get(f"/api/v1/instance/orgs/{source.org.id}/export", headers=headers)
        ).json()
        payload["schema_revision"] = "somewhere-else"
        response = await client.post(
            "/api/v1/instance/orgs/import",
            json={"slug": "mismatch-copy", "data": payload},
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["error"]["message"] == "errors.import_schema_mismatch"


# --------------------------------------------------------------------------- #
# Custom-domain claim & verify (tenant manager surface)
# --------------------------------------------------------------------------- #
async def test_domain_claim_verify_and_uniqueness(client_for, monkeypatch) -> None:
    a = await make_tenant("dom-a")
    b = await make_tenant("dom-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    published: dict[str, list[str]] = {}

    async def fake_txt(name: str) -> list[str]:
        return published.get(name, [])

    from app.core import domains as domains_module

    monkeypatch.setattr(domains_module.dnscheck, "txt_records", fake_txt)

    async with client_for(a.host) as client:
        # Hosts under the base domain are routed by slug — not claimable.
        under_base = await client.post(
            "/api/v1/meta/tenant/domain", json={"domain": "dom-b.localhost"}, headers=a_headers
        )
        assert under_base.status_code == 422

        claimed = await client.post(
            "/api/v1/meta/tenant/domain", json={"domain": "crm.agency.test"}, headers=a_headers
        )
        assert claimed.status_code == 200
        challenge = claimed.json()
        assert challenge["txt_record_name"] == "_schakl-challenge.crm.agency.test"
        token = challenge["txt_record_value"]

        # Verification fails until the TXT record exists…
        failed = await client.post("/api/v1/meta/tenant/domain/verify", headers=a_headers)
        assert failed.status_code == 400
        assert failed.json()["error"]["message"] == "errors.domain_verification_failed"

        published["_schakl-challenge.crm.agency.test"] = ["something-else", token]
        verified = await client.post("/api/v1/meta/tenant/domain/verify", headers=a_headers)
        assert verified.status_code == 200
        assert verified.json()["custom_domain"] == "crm.agency.test"
        assert verified.json()["pending_domain"] is None

    # The verified domain now resolves to org A.
    async with client_for("crm.agency.test") as client:
        assert (await client.get("/api/v1/meta/tenant")).json()["slug"] == "dom-a"

    # Org B cannot claim the same domain (global uniqueness, explicit unscoped check).
    async with client_for(b.host) as client:
        conflict = await client.post(
            "/api/v1/meta/tenant/domain", json={"domain": "crm.agency.test"}, headers=b_headers
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["message"] == "errors.domain_taken"

    # Members cannot manage domains.
    member = await make_tenant("dom-member", role="member")
    async with client_for(member.host) as client:
        denied = await client.get(
            "/api/v1/meta/tenant/domain", headers=await auth_cookie(member.user)
        )
        assert denied.status_code == 403
