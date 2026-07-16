"""Cloud posture (epic #199): the deployment gate, the org-issued service PIN, the
key-authenticated provisioning API with plans/trials, the instance-provided e-mail choice,
the custom-domain ingress renderer, and the cloud first-run wizard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.core.cloud.ingress import render_fragment, sync_ingress, verified_domains
from app.core.cloud.models import ServiceAccessGrant
from app.core.cloud.plans import suspend_expired_trials
from app.core.email.senders import OutgoingEmail
from app.core.email.service import email_configured, send_org_email
from app.core.models import Org, OrgStatus
from app.db import async_session_maker, set_current_org
from tests.conftest import Tenant, auth_cookie, make_tenant


@pytest.fixture
def cloud_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deployment", "cloud")
    monkeypatch.setattr(settings, "instance_admin_enabled", True)


@pytest.fixture
def instance_email(monkeypatch) -> None:
    monkeypatch.setattr(settings, "instance_email_enabled", True)
    monkeypatch.setattr(settings, "instance_email_provider", "smtp")
    monkeypatch.setattr(settings, "instance_email_from", "post@cloud.example")
    monkeypatch.setattr(settings, "instance_email_from_name", "Cloud")
    monkeypatch.setattr(settings, "instance_email_host", "smtp.cloud.example")


async def make_instance_owner(tenant: Tenant) -> None:
    async with async_session_maker() as session:
        user = await session.get(User, tenant.user.id)
        user.is_superuser = True
        await session.commit()
    tenant.user.is_superuser = True


# --------------------------------------------------------------------------- #
# The posture gate
# --------------------------------------------------------------------------- #
async def test_cloud_surface_hidden_on_self_host(client_for) -> None:
    admin = await make_tenant("sh-gate")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)
    async with client_for(admin.host) as client:
        assert (await client.get("/api/v1/instance/me", headers=headers)).status_code == 404
        assert (
            await client.get("/api/v1/settings/service-access", headers=headers)
        ).status_code == 404
        assert (
            await client.get(
                "/api/v1/instance/provisioning/orgs", headers={"X-API-Key": "schakl_x_y"}
            )
        ).status_code == 404


async def test_self_host_org_detail_needs_no_pin(client_for, monkeypatch) -> None:
    monkeypatch.setattr(settings, "instance_admin_enabled", True)
    admin = await make_tenant("sh-nopin")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)
    async with client_for(admin.host) as client:
        detail = await client.get(f"/api/v1/instance/orgs/{admin.org.id}", headers=headers)
        assert detail.status_code == 200


# --------------------------------------------------------------------------- #
# Service PIN
# --------------------------------------------------------------------------- #
async def test_service_pin_gates_org_data_and_unlocks(client_for, cloud_mode) -> None:
    admin = await make_tenant("cl-admin")
    await make_instance_owner(admin)
    tenant = await make_tenant("cl-tenant", email="tenant-owner@example.com")
    admin_headers = await auth_cookie(admin.user)
    tenant_headers = await auth_cookie(tenant.user)

    async with client_for(admin.host) as admin_client:
        # Tenant data is locked without a claimed PIN: detail, export, impersonation.
        for call in (
            admin_client.get(f"/api/v1/instance/orgs/{tenant.org.id}", headers=admin_headers),
            admin_client.get(
                f"/api/v1/instance/orgs/{tenant.org.id}/export", headers=admin_headers
            ),
            admin_client.post(
                f"/api/v1/instance/orgs/{tenant.org.id}/impersonate",
                headers=admin_headers,
                json={"user_id": str(tenant.user.id), "minutes": 5},
            ),
        ):
            response = await call
            assert response.status_code == 403
            assert response.json()["error"]["message"] == "errors.service_pin_required"

        # The org list itself (names, status) stays readable — operations need it.
        assert (
            await admin_client.get("/api/v1/instance/orgs", headers=admin_headers)
        ).status_code == 200

    # The org issues a PIN on its own host…
    async with client_for(tenant.host) as tenant_client:
        issued = await tenant_client.post(
            "/api/v1/settings/service-access", headers=tenant_headers
        )
        assert issued.status_code == 201
        pin = issued.json()["pin"]
        assert len([c for c in pin if c.isdigit()]) == 12

        status = await tenant_client.get(
            "/api/v1/settings/service-access", headers=tenant_headers
        )
        assert status.json()["active"] is True
        assert status.json()["claimed"] is False

    async with client_for(admin.host) as admin_client:
        # …a wrong PIN is refused…
        wrong = await admin_client.post(
            f"/api/v1/instance/orgs/{tenant.org.id}/service-access",
            headers=admin_headers,
            json={"pin": "0000-0000-0000"},
        )
        assert wrong.status_code == 403
        assert wrong.json()["error"]["message"] == "errors.service_pin_invalid"

        # …the right one unlocks exactly this org for this owner…
        claimed = await admin_client.post(
            f"/api/v1/instance/orgs/{tenant.org.id}/service-access",
            headers=admin_headers,
            json={"pin": pin},
        )
        assert claimed.status_code == 200
        assert claimed.json()["access_until"] is not None

        detail = await admin_client.get(
            f"/api/v1/instance/orgs/{tenant.org.id}", headers=admin_headers
        )
        assert detail.status_code == 200
        assert detail.json()["slug"] == "cl-tenant"

        # …and not the admin's own other org (the PIN is org-bound).
        own = await admin_client.get(
            f"/api/v1/instance/orgs/{admin.org.id}", headers=admin_headers
        )
        assert own.status_code == 403


async def test_service_pin_expiry_and_revocation(client_for, cloud_mode) -> None:
    admin = await make_tenant("cl-exp-admin")
    await make_instance_owner(admin)
    tenant = await make_tenant("cl-exp-tenant", email="exp-owner@example.com")
    admin_headers = await auth_cookie(admin.user)
    tenant_headers = await auth_cookie(tenant.user)

    async with client_for(tenant.host) as tenant_client:
        pin = (
            await tenant_client.post(
                "/api/v1/settings/service-access", headers=tenant_headers
            )
        ).json()["pin"]

    async with client_for(admin.host) as admin_client:
        assert (
            await admin_client.post(
                f"/api/v1/instance/orgs/{tenant.org.id}/service-access",
                headers=admin_headers,
                json={"pin": pin},
            )
        ).status_code == 200

    # The org revokes → access is gone immediately.
    async with client_for(tenant.host) as tenant_client:
        revoked = await tenant_client.delete(
            "/api/v1/settings/service-access", headers=tenant_headers
        )
        assert revoked.status_code == 200
        assert revoked.json()["active"] is False

    async with client_for(admin.host) as admin_client:
        blocked = await admin_client.get(
            f"/api/v1/instance/orgs/{tenant.org.id}", headers=admin_headers
        )
        assert blocked.status_code == 403

    # A fresh claimed grant that has *expired* no longer unlocks either.
    async with client_for(tenant.host) as tenant_client:
        pin = (
            await tenant_client.post(
                "/api/v1/settings/service-access", headers=tenant_headers
            )
        ).json()["pin"]
    async with client_for(admin.host) as admin_client:
        assert (
            await admin_client.post(
                f"/api/v1/instance/orgs/{tenant.org.id}/service-access",
                headers=admin_headers,
                json={"pin": pin},
            )
        ).status_code == 200
    async with async_session_maker() as session:
        grant = await session.scalar(
            select(ServiceAccessGrant).where(
                ServiceAccessGrant.org_id == tenant.org.id,
                ServiceAccessGrant.revoked_at.is_(None),
            )
        )
        grant.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    async with client_for(admin.host) as admin_client:
        assert (
            await admin_client.get(
                f"/api/v1/instance/orgs/{tenant.org.id}", headers=admin_headers
            )
        ).status_code == 403


async def test_service_pin_needs_permission(client_for, cloud_mode) -> None:
    tenant = await make_tenant("cl-perm", role="member")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        assert (
            await client.post("/api/v1/settings/service-access", headers=headers)
        ).status_code == 403


# --------------------------------------------------------------------------- #
# Provisioning API + plans
# --------------------------------------------------------------------------- #
async def mint_instance_key(client_for, cloud_mode_headers) -> str:  # noqa: ANN001
    """Create an instance API key through the console endpoint; returns the plaintext."""
    client, headers = cloud_mode_headers
    created = await client.post(
        "/api/v1/instance/api-keys", headers=headers, json={"name": "billing"}
    )
    assert created.status_code == 201
    return created.json()["secret"]


async def test_provisioning_end_to_end(client_for, cloud_mode) -> None:
    admin = await make_tenant("cl-prov-admin")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)

    async with client_for(admin.host) as client:
        secret = await mint_instance_key(client_for, (client, headers))

        # No/garbage credential → 401; a session cookie alone does not authenticate it.
        assert (
            await client.get("/api/v1/instance/provisioning/orgs")
        ).status_code == 401
        assert (
            await client.get(
                "/api/v1/instance/provisioning/orgs",
                headers={"X-API-Key": "schakl_dead_beef"},
            )
        ).status_code == 401

        key_headers = {"X-API-Key": secret}
        created = await client.post(
            "/api/v1/instance/provisioning/orgs",
            headers=key_headers,
            json={
                "name": "Trial Agency",
                "slug": "trial-agency",
                "owner_email": "boss@trial.example",
                "owner_password": "supersecret1",
                "plan": "trial",
                "trial_days": 30,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["plan"] == "trial"
        assert body["url"] == "http://trial-agency.localhost"
        ends = datetime.fromisoformat(body["trial_ends_at"])
        assert timedelta(days=29) < ends - datetime.now(UTC) < timedelta(days=31)

        # The provisioned owner can log in with the handed-over password on the org host.
        async with client_for("trial-agency.localhost") as org_client:
            login = await org_client.post(
                "/api/v1/auth/login",
                data={"username": "boss@trial.example", "password": "supersecret1"},
            )
            assert login.status_code in (200, 204)

        # …and is a plain org owner, never the platform superuser (issue #201).
        async with async_session_maker() as session:
            owner = await session.scalar(
                select(User).where(User.email == "boss@trial.example")
            )
            assert owner is not None and not owner.is_superuser

        # Slug collision is a clean 409.
        dup = await client.post(
            "/api/v1/instance/provisioning/orgs",
            headers=key_headers,
            json={
                "name": "Dup",
                "slug": "trial-agency",
                "owner_email": "dup@trial.example",
            },
        )
        assert dup.status_code == 409

        # "No expiration" is a real choice: unlimited carries no trial clock.
        forever = await client.post(
            "/api/v1/instance/provisioning/orgs",
            headers=key_headers,
            json={
                "name": "Forever",
                "slug": "forever",
                "owner_email": "boss@forever.example",
                "plan": "unlimited",
            },
        )
        assert forever.status_code == 201
        assert forever.json()["plan"] == "unlimited"
        assert forever.json()["trial_ends_at"] is None

        # Trial → standard on payment: the clock clears.
        paid = await client.patch(
            "/api/v1/instance/provisioning/orgs/trial-agency/plan",
            headers=key_headers,
            json={"plan": "standard"},
        )
        assert paid.status_code == 200
        assert paid.json()["plan"] == "standard"
        assert paid.json()["trial_ends_at"] is None

        # Billing drives suspension without tenant consent — and back.
        suspended = await client.post(
            "/api/v1/instance/provisioning/orgs/trial-agency/suspend", headers=key_headers
        )
        assert suspended.json()["status"] == "suspended"
        active = await client.post(
            "/api/v1/instance/provisioning/orgs/trial-agency/activate", headers=key_headers
        )
        assert active.json()["status"] == "active"

        # A revoked key stops working immediately.
        keys = await client.get("/api/v1/instance/api-keys", headers=headers)
        key_id = keys.json()[0]["id"]
        assert (
            await client.post(
                f"/api/v1/instance/api-keys/{key_id}/revoke", headers=headers
            )
        ).status_code == 200
        assert (
            await client.get("/api/v1/instance/provisioning/orgs", headers=key_headers)
        ).status_code == 401


async def test_trial_expiry_suspends_only_expired_trials(client_for, cloud_mode) -> None:
    admin = await make_tenant("cl-cron-admin")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)
    async with client_for(admin.host) as client:
        secret = await mint_instance_key(client_for, (client, headers))
        key_headers = {"X-API-Key": secret}
        for slug, plan in (("expired-trial", "trial"), ("fresh-trial", "trial"),
                           ("forever-org", "unlimited")):
            created = await client.post(
                "/api/v1/instance/provisioning/orgs",
                headers=key_headers,
                json={
                    "name": slug,
                    "slug": slug,
                    "owner_email": f"owner@{slug}.example",
                    "plan": plan,
                },
            )
            assert created.status_code == 201

    async with async_session_maker() as session:
        org = await session.scalar(select(Org).where(Org.slug == "expired-trial"))
        org.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

    async with async_session_maker() as session:
        assert await suspend_expired_trials(session) == 1
        await session.commit()

    async with async_session_maker() as session:
        statuses = {
            org.slug: org.status
            for org in (await session.execute(select(Org))).scalars()
            if org.slug.endswith(("-trial", "-org"))
        }
    assert statuses["expired-trial"] == OrgStatus.SUSPENDED.value
    assert statuses["fresh-trial"] == OrgStatus.ACTIVE.value
    assert statuses["forever-org"] == OrgStatus.ACTIVE.value


# --------------------------------------------------------------------------- #
# Cloud first-run + /meta/instance
# --------------------------------------------------------------------------- #
async def test_cloud_setup_creates_instance_owner_only(client_for, cloud_mode) -> None:
    async with client_for("localhost") as client:
        meta = await client.get("/api/v1/meta/instance")
        assert meta.status_code == 200
        assert meta.json() == {
            "deployment": "cloud",
            "is_instance_host": True,
            "needs_setup": True,
            "base_domain": "localhost",
        }

        created = await client.post(
            "/api/v1/setup",
            json={
                "owner_email": "operator@cloud.example",
                "owner_password": "supersecret1",
                "owner_full_name": "Operator",
            },
        )
        assert created.status_code == 201

        # Setup mints the superuser and nothing else — no org exists.
        again = await client.post(
            "/api/v1/setup",
            json={"owner_email": "x@y.example", "owner_password": "supersecret1"},
        )
        assert again.status_code == 409
        assert (await client.get("/api/v1/meta/instance")).json()["needs_setup"] is False

    async with async_session_maker() as session:
        operator = await session.scalar(
            select(User).where(User.email == "operator@cloud.example")
        )
        assert operator is not None and operator.is_superuser
        assert (await session.execute(select(Org))).scalars().all() == []


async def test_meta_instance_on_tenant_host_and_self_host(client_for, cloud_mode) -> None:
    tenant = await make_tenant("cl-meta")
    async with client_for(tenant.host) as client:
        meta = (await client.get("/api/v1/meta/instance")).json()
        assert meta["is_instance_host"] is False
        assert meta["deployment"] == "cloud"


async def test_meta_instance_self_host_posture(client_for) -> None:
    await make_tenant("sh-meta")
    async with client_for("localhost") as client:
        meta = (await client.get("/api/v1/meta/instance")).json()
        assert meta["deployment"] == "self_hosted"
        assert meta["is_instance_host"] is False


# --------------------------------------------------------------------------- #
# Instance-provided e-mail
# --------------------------------------------------------------------------- #
async def test_send_falls_back_to_instance_transport(instance_email, monkeypatch) -> None:
    tenant = await make_tenant("cl-mail")
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001
        sent.append((provider, config, sender, message))
        return True, None

    monkeypatch.setattr("app.core.email.service.send_email", fake_send)
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        assert await email_configured(session, tenant.org.id) is True
        ok, error = await send_org_email(
            session, tenant.org.id, OutgoingEmail(to="a@b.example", subject="s", text="t")
        )
    assert ok and error is None
    provider, config, sender, _ = sent[0]
    assert provider == "smtp"
    assert config["host"] == "smtp.cloud.example"
    assert sender.from_email == "post@cloud.example"
    # Displayed as the org's own brand, sent from the instance address.
    assert sender.from_name == "Cl-Mail"


async def test_no_transport_still_errors_without_instance_email() -> None:
    tenant = await make_tenant("cl-nomail")
    async with async_session_maker() as session:
        assert await email_configured(session, tenant.org.id) is False
        ok, error = await send_org_email(
            session, tenant.org.id, OutgoingEmail(to="a@b.example", subject="s", text="t")
        )
    assert not ok and error == "errors.email_not_configured"


async def test_explicit_instance_provider_choice(
    client_for, instance_email, monkeypatch
) -> None:
    tenant = await make_tenant("cl-mailset")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        saved = await client.put(
            "/api/v1/settings/email",
            headers=headers,
            json={"provider": "instance", "from_name": "Bureau X"},
        )
        assert saved.status_code == 200
        body = saved.json()
        assert body["provider"] == "instance"
        assert body["from_email"] == "post@cloud.example"

        # Unavailable instance transport → the choice is refused, not stored broken.
        monkeypatch.setattr(settings, "instance_email_enabled", False)
        refused = await client.put(
            "/api/v1/settings/email",
            headers=headers,
            json={"provider": "instance", "from_name": "Bureau X"},
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.instance_email_unavailable"


# --------------------------------------------------------------------------- #
# Custom-domain ingress rendering (#202)
# --------------------------------------------------------------------------- #
async def test_ingress_fragment_only_lists_verified_domains(
    cloud_mode, monkeypatch, tmp_path: Path
) -> None:
    verified = await make_tenant("cl-dom-a")
    pending = await make_tenant("cl-dom-b")
    async with async_session_maker() as session:
        org_a = await session.get(Org, verified.org.id)
        org_a.custom_domain = "crm.agency-a.example"
        org_a.custom_domain_verified_at = datetime.now(UTC)
        org_b = await session.get(Org, pending.org.id)
        org_b.pending_domain = "crm.agency-b.example"
        await session.commit()

    monkeypatch.setattr(settings, "cloud_ingress_dir", str(tmp_path))
    async with async_session_maker() as session:
        domains = await verified_domains(session)
        assert domains == ["crm.agency-a.example"]
        path = await sync_ingress(session)

    assert path is not None and path.name == "custom-domains.yml"
    content = path.read_text()
    assert "Host(`crm.agency-a.example`)" in content
    assert "agency-b" not in content
    assert "certResolver: letsencrypt" in content

    empty = render_fragment([])
    assert "http: {}" in empty
