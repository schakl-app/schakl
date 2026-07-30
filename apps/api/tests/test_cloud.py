"""Cloud posture (epic #199): the deployment gate, the org-issued service PIN, the
key-authenticated provisioning API with plans/trials, the instance-provided e-mail choice,
the custom-domain ingress renderer, and the cloud first-run wizard."""

from __future__ import annotations

import json as json_module
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.core.cloud import cloudflare as cf
from app.core.cloud.ingress import render_fragment, sync_ingress, verified_domains
from app.core.cloud.models import ServiceAccessGrant
from app.core.cloud.plans import suspend_expired_trials
from app.core.email.senders import OutgoingEmail
from app.core.email.service import email_configured, send_org_email
from app.core.instance import service as org_service
from app.core.models import Org, OrgStatus
from app.db import async_session_maker, set_current_org
from app.errors import AppError
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


# --------------------------------------------------------------------------- #
# Cloudflare for SaaS custom hostnames (#199)
# --------------------------------------------------------------------------- #
class _FakeCloudflare:
    """A minimal stand-in for the zone's custom-hostname API.

    Deliberately mimics Cloudflare's **substring** hostname filter, so the exact-match guard in
    ``find_custom_hostname`` is actually exercised rather than assumed.
    """

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.dns: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict] = []
        self.dns_bodies: list[dict] = []
        #: Statuses to answer before behaving normally — one entry consumed per request.
        self.fail_with: list[int] = []
        #: ``(method, status, message)`` refusals in Cloudflare's own words. The first entry
        #: matching the request's method (``"*"`` matches any) answers it and is consumed — so a
        #: test can refuse the *create* without the preceding lookup swallowing it.
        self.refusals: list[tuple[str, int, str]] = []
        self._seq = 0

    def seed_dns(self, name: str, *, record_id: str = "seeded") -> None:
        self.dns[record_id] = {"id": record_id, "name": name, "type": "CNAME"}

    def seed_hostname(self, hostname: str, *, record_id: str = "manual") -> None:
        """A custom hostname the operator added by hand in the dashboard (#293)."""
        self.records[record_id] = {"id": record_id, "hostname": hostname, "status": "active"}

    def set_state(
        self,
        hostname_id: str,
        *,
        status: str | None = None,
        ssl_status: str | None = None,
        expires_on: str | None = None,
        validation_errors: list[str] | None = None,
    ) -> None:
        """Drive the lifecycle a real Cloudflare would (#291): hostname/SSL status flips,
        certificate expiry, validation errors."""
        record = self.records[hostname_id]
        if status is not None:
            record["status"] = status
        ssl = record.setdefault("ssl", {})
        if ssl_status is not None:
            ssl["status"] = ssl_status
        if expires_on is not None:
            ssl["expires_on"] = expires_on
        if validation_errors is not None:
            ssl["validation_errors"] = [{"message": message} for message in validation_errors]

    @staticmethod
    def _ok(result) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "errors": [], "result": result})

    @staticmethod
    def _gone() -> httpx.Response:
        return httpx.Response(404, json={"success": False, "errors": [{"message": "not found"}]})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        for index, (method, status, message) in enumerate(self.refusals):
            if method in ("*", request.method):
                self.refusals.pop(index)
                return httpx.Response(
                    status, json={"success": False, "errors": [{"message": message}]}
                )
        if self.fail_with:
            return httpx.Response(
                self.fail_with.pop(0), json={"success": False, "errors": [{"message": "nope"}]}
            )
        path = request.url.path
        body = json_module.loads(request.content or b"{}") if request.content else {}

        if "/custom_hostnames" in path:
            if request.method == "GET":
                if not path.endswith("/custom_hostnames"):
                    # Detail read by id (#291) — the lifecycle refresh path.
                    found = self.records.get(path.rsplit("/", 1)[-1])
                    return self._ok(found) if found else self._gone()
                wanted = request.url.params.get("hostname", "")
                # Cloudflare's filter is a substring match — mimic it, so the exact-match
                # guard in find_custom_hostname is genuinely exercised.
                return self._ok([r for r in self.records.values() if wanted in r["hostname"]])
            if request.method == "POST":
                self.bodies.append(body)
                self._seq += 1
                record = {
                    "id": f"ch{self._seq}",
                    "hostname": body["hostname"],
                    # A fresh custom hostname is never active: HTTP DCV still has to run.
                    "status": "pending",
                    "ssl": {"status": "pending_validation"},
                }
                self.records[record["id"]] = record
                return httpx.Response(
                    201, json={"success": True, "errors": [], "result": record}
                )
            if request.method == "DELETE":
                gone = self.records.pop(path.rsplit("/", 1)[-1], None)
                return self._ok({}) if gone else self._gone()

        if "/dns_records" in path:
            if request.method == "GET":
                # The real DNS `name` filter is exact; the wildcard is stored under the literal
                # name "*.<zone>" and therefore never matches a specific subdomain query.
                wanted = request.url.params.get("name", "")
                return self._ok([r for r in self.dns.values() if r["name"] == wanted])
            if request.method == "POST":
                self.dns_bodies.append(body)
                self._seq += 1
                record = {"id": f"dns{self._seq}", "name": body["name"], "type": body["type"]}
                self.dns[record["id"]] = record
                return httpx.Response(
                    201, json={"success": True, "errors": [], "result": record}
                )
            if request.method == "DELETE":
                gone = self.dns.pop(path.rsplit("/", 1)[-1], None)
                return self._ok({}) if gone else self._gone()

        return httpx.Response(404, json={"success": False, "errors": [{"message": "no route"}]})


@pytest.fixture
def cloudflare(monkeypatch, cloud_mode) -> _FakeCloudflare:
    fake = _FakeCloudflare()
    monkeypatch.setattr(settings, "cloud_cf_api_token", "cf-token-never-logged")
    monkeypatch.setattr(settings, "cloud_cf_zone_id", "zone-123")
    # Unset, which is the *default* posture and the only one a Free/Pro/Business zone can use
    # (#293). Tests that need the Enterprise SNI rewrite set it themselves.
    monkeypatch.setattr(settings, "cloud_cf_origin_sni", None)
    monkeypatch.setattr(cf, "_transport", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def published_txt(monkeypatch) -> dict[str, list[str]]:
    """Stand in for the DNS TXT challenge lookup, as test_instance_admin does."""
    published: dict[str, list[str]] = {}

    async def fake_txt(name: str) -> list[str]:
        return published.get(name, [])

    from app.core import domains as domains_module

    monkeypatch.setattr(domains_module.dnscheck, "txt_records", fake_txt)
    return published


async def _claim_and_publish(client, headers, published, domain: str) -> None:
    claimed = await client.post(
        "/api/v1/meta/tenant/domain", json={"domain": domain}, headers=headers
    )
    assert claimed.status_code == 200
    published[f"_schakl-challenge.{domain}"] = [claimed.json()["txt_record_value"]]


def test_cloudflare_off_unless_fully_configured(monkeypatch, cloud_mode) -> None:
    monkeypatch.setattr(settings, "cloud_cf_api_token", None)
    monkeypatch.setattr(settings, "cloud_cf_zone_id", "zone-123")
    assert cf.cloudflare_configured() is False
    monkeypatch.setattr(settings, "cloud_cf_api_token", "tok")
    monkeypatch.setattr(settings, "cloud_cf_zone_id", None)
    assert cf.cloudflare_configured() is False
    # Fully configured, but self-host: the integration is a cloud posture, never a self-host one.
    monkeypatch.setattr(settings, "cloud_cf_zone_id", "zone-123")
    assert cf.cloudflare_configured() is True
    monkeypatch.setattr(settings, "deployment", "self_hosted")
    assert cf.cloudflare_configured() is False


async def test_ensure_custom_hostname_omits_the_enterprise_only_sni_rewrite(cloudflare) -> None:
    """The default request, in full (#293).

    ``custom_origin_sni`` must be *absent*, not empty: an explicit SNI rewrite needs an
    Enterprise entitlement, and sending it on a Free/Pro/Business zone fails the whole create.
    Cloudflare presents the custom origin server's own name as SNI anyway, which is the value
    this instance would have derived — so the routing is unchanged and Full (strict) still
    validates against the wildcard origin certificate.
    """
    hostname_id = await cf.ensure_custom_hostname("crm.klant.test")
    assert hostname_id == "ch1"
    assert cloudflare.bodies[0] == {
        "hostname": "crm.klant.test",
        "ssl": {"method": "http", "type": "dv", "settings": {"min_tls_version": "1.2"}},
        "custom_origin_server": "edge.localhost",
    }


async def test_configured_sni_rewrite_never_moves_the_origin_server(cloudflare, monkeypatch):
    """An entitled operator may rewrite SNI to a name that is *not* the origin server. Deriving
    one value from the other would silently re-route the origin to it."""
    monkeypatch.setattr(settings, "cloud_cf_origin_sni", "sni.anders.test")
    await cf.ensure_custom_hostname("crm.klant.test")
    body = cloudflare.bodies[0]
    assert body["custom_origin_sni"] == "sni.anders.test"
    assert body["custom_origin_server"] == "edge.localhost"


async def test_an_sni_entitlement_refusal_says_what_the_operator_must_change(
    cloudflare, monkeypatch
) -> None:
    """Cloudflare's own refusal, plus the fix — and never the token."""
    monkeypatch.setattr(settings, "cloud_cf_origin_sni", "edge.schakl.test")
    cloudflare.refusals = [
        ("POST", 403, "Access to setting a custom origin SNI has not been granted")
    ]
    with pytest.raises(cf.CloudflareNotEntitledError) as caught:
        await cf.ensure_custom_hostname("crm.klant.test")
    message = str(caught.value)
    assert "Access to setting a custom origin SNI has not been granted" in message
    assert "SCHAKL_CLOUD_CF_ORIGIN_SNI" in message
    assert "Enterprise" in message
    assert "cf-token-never-logged" not in message


async def test_an_entitlement_refusal_is_not_retried(cloudflare) -> None:
    """A missing scope is permanent: a second attempt cannot succeed, and burning it hides
    nothing. One request, one refusal."""
    cloudflare.refusals = [("GET", 403, "Actor is not authorized to perform this action")]
    with pytest.raises(cf.CloudflareNotEntitledError):
        await cf.find_custom_hostname("crm.klant.test")
    assert len(cloudflare.calls) == 1


async def test_a_transient_failure_is_not_read_as_an_entitlement_problem(cloudflare) -> None:
    """The marker list must stay narrow — a 500 that gave up twice is still retryable."""
    cloudflare.fail_with = [500, 500]
    with pytest.raises(cf.CloudflareError) as caught:
        await cf.ensure_custom_hostname("crm.klant.test")
    assert not isinstance(caught.value, cf.CloudflareNotEntitledError)


async def test_ensure_adopts_a_hand_created_hostname_without_creating(cloudflare) -> None:
    """The documented workaround for #293: the operator adds the hostname in the dashboard, and
    the next verify adopts it instead of re-issuing the request that failed."""
    cloudflare.seed_hostname("crm.klant.test", record_id="manual")
    assert await cf.ensure_custom_hostname("crm.klant.test") == "manual"
    assert cloudflare.bodies == []
    assert [method for method, _ in cloudflare.calls] == ["GET"]


async def test_ensure_custom_hostname_adopts_only_an_exact_match(cloudflare) -> None:
    """Cloudflare's filter is a substring match, so `klant.test` lists `crm.klant.test` too.
    Adopting that would point the wrong org's domain at the wrong record."""
    await cf.ensure_custom_hostname("crm.klant.test")
    assert len(cloudflare.records) == 1

    # A different, shorter hostname must NOT adopt the existing record — it creates its own.
    second = await cf.ensure_custom_hostname("klant.test")
    assert second == "ch2"
    assert len(cloudflare.records) == 2

    # The same hostname again adopts rather than duplicating.
    again = await cf.ensure_custom_hostname("crm.klant.test")
    assert again == "ch1"
    assert len(cloudflare.records) == 2


async def test_delete_custom_hostname_is_idempotent(cloudflare) -> None:
    hostname_id = await cf.ensure_custom_hostname("crm.klant.test")
    await cf.delete_custom_hostname(hostname_id)
    assert cloudflare.records == {}
    # Already gone is the state the caller wanted — a 404 must not raise.
    await cf.delete_custom_hostname(hostname_id)


async def test_transient_status_is_retried_once(cloudflare) -> None:
    cloudflare.fail_with = [503]
    hostname_id = await cf.ensure_custom_hostname("crm.klant.test")
    assert hostname_id == "ch1"

    # Two failures in a row is a real failure, and the error carries Cloudflare's own text.
    cloudflare.fail_with = [500, 500]
    with pytest.raises(cf.CloudflareError) as caught:
        await cf.ensure_custom_hostname("other.klant.test")
    assert "cf-token-never-logged" not in str(caught.value)


async def test_domain_verify_registers_hostname_and_clear_removes_it(
    client_for, cloudflare, published_txt
) -> None:
    tenant = await make_tenant("cf-dom")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _claim_and_publish(client, headers, published_txt, "crm.klant.test")
        verified = await client.post("/api/v1/meta/tenant/domain/verify", headers=headers)
        assert verified.status_code == 200
        assert verified.json()["custom_domain"] == "crm.klant.test"

    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        assert org.cf_hostname_id == "ch1"
    assert cloudflare.records["ch1"]["hostname"] == "crm.klant.test"

    async with client_for(tenant.host) as client:
        cleared = await client.delete("/api/v1/meta/tenant/domain", headers=headers)
        assert cleared.status_code == 200

    assert cloudflare.records == {}
    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        assert org.cf_hostname_id is None
        assert org.custom_domain is None


async def test_verify_fails_closed_when_cloudflare_is_down(
    client_for, cloudflare, published_txt
) -> None:
    """A domain must never read as verified while the edge has no certificate for it."""
    tenant = await make_tenant("cf-down")
    headers = await auth_cookie(tenant.user)
    cloudflare.fail_with = [500, 500, 500, 500]
    async with client_for(tenant.host) as client:
        await _claim_and_publish(client, headers, published_txt, "crm.stuk.test")
        failed = await client.post("/api/v1/meta/tenant/domain/verify", headers=headers)
        assert failed.status_code == 502
        assert failed.json()["error"]["message"] == "errors.cloudflare_failed"

        status = await client.get("/api/v1/meta/tenant/domain", headers=headers)
        assert status.json()["custom_domain"] is None
        assert status.json()["pending_domain"] == "crm.stuk.test"

    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        assert org.custom_domain is None
        assert org.cf_hostname_id is None


async def test_verify_reports_an_entitlement_problem_as_its_own_error(
    client_for, cloudflare, published_txt
) -> None:
    """Not ``errors.cloudflare_failed`` — that message tells the tenant to try again in a moment,
    and no number of retries fixes a token scope or a plan (#293)."""
    tenant = await make_tenant("cf-plan")
    headers = await auth_cookie(tenant.user)
    cloudflare.refusals = [
        ("POST", 403, "Access to setting a custom origin SNI has not been granted")
    ]
    async with client_for(tenant.host) as client:
        await _claim_and_publish(client, headers, published_txt, "crm.plan.test")
        failed = await client.post("/api/v1/meta/tenant/domain/verify", headers=headers)
        assert failed.status_code == 502
        assert failed.json()["error"]["message"] == "errors.cloudflare_not_entitled"

    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        assert org.custom_domain is None
        assert org.cf_hostname_id is None


async def test_self_host_verify_never_calls_cloudflare(
    client_for, monkeypatch, published_txt
) -> None:
    fake = _FakeCloudflare()
    monkeypatch.setattr(settings, "cloud_cf_api_token", "tok")
    monkeypatch.setattr(settings, "cloud_cf_zone_id", "zone-123")
    monkeypatch.setattr(cf, "_transport", httpx.MockTransport(fake.handler))
    # deployment stays "self_hosted" — no cloud_mode fixture here.
    tenant = await make_tenant("cf-selfhost")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _claim_and_publish(client, headers, published_txt, "crm.zelf.test")
        verified = await client.post("/api/v1/meta/tenant/domain/verify", headers=headers)
        assert verified.status_code == 200

    assert fake.calls == []
    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        assert org.custom_domain == "crm.zelf.test"
        assert org.cf_hostname_id is None


# --------------------------------------------------------------------------- #
# Automatic subdomain provisioning (#199)
# --------------------------------------------------------------------------- #
async def _make_org(actor_id, **kwargs):
    async with async_session_maker() as session:
        actor = await session.get(User, actor_id)
        org = await org_service.create_org(session, actor, **kwargs)
        await session.commit()
        return org.id


async def test_create_org_provisions_a_proxied_subdomain(cloudflare) -> None:
    actor = await make_tenant("cf-prov")
    org_id = await _make_org(actor.user.id, name="Klant BV", slug="klantbv")

    body = cloudflare.dns_bodies[0]
    assert body["name"] == "klantbv.localhost"
    assert body["type"] == "CNAME"
    assert body["content"] == "edge.localhost"
    # Unproxied would expose the origin IP and bypass the edge entirely.
    assert body["proxied"] is True

    async with async_session_maker() as session:
        stored = await session.get(Org, org_id)
        assert stored.cf_dns_record_id == "dns1"


async def test_subdomain_already_in_the_zone_is_rejected(cloudflare) -> None:
    """A name can be free as a slug and still taken in DNS — an infrastructure record, or a
    leftover from an org purged elsewhere in the same zone. The zone is the authority."""
    cloudflare.seed_dns("bezet.localhost")
    actor = await make_tenant("cf-clash")

    with pytest.raises(AppError) as caught:
        await _make_org(actor.user.id, name="Bezet", slug="bezet")
    assert caught.value.message_key == "errors.subdomain_taken"
    assert caught.value.status_code == 409
    # Nothing was created: the check runs before any row or record is written.
    assert cloudflare.dns_bodies == []


async def test_wildcard_record_does_not_block_a_slug(cloudflare) -> None:
    """The zone routes *.<base_domain> by wildcard. That must not read as "every name taken"."""
    cloudflare.seed_dns("*.localhost")
    actor = await make_tenant("cf-wild")
    org_id = await _make_org(actor.user.id, name="Vrij", slug="vrij")
    async with async_session_maker() as session:
        assert (await session.get(Org, org_id)).cf_dns_record_id is not None


async def test_reslug_moves_the_subdomain_record(cloudflare) -> None:
    actor = await make_tenant("cf-reslug")
    org_id = await _make_org(actor.user.id, name="Oud", slug="oudnaam")
    async with async_session_maker() as session:
        assert (await session.get(Org, org_id)).cf_dns_record_id == "dns1"

    async with async_session_maker() as session:
        who = await session.get(User, actor.user.id)
        org = await session.get(Org, org_id)
        await org_service.update_org(session, who, org, slug="nieuwenaam")
        await session.commit()

    names = {r["name"] for r in cloudflare.dns.values()}
    assert names == {"nieuwenaam.localhost"}  # the old record is gone, the new one is live
    async with async_session_maker() as session:
        stored = await session.get(Org, org_id)
        assert stored.slug == "nieuwenaam"
        assert stored.cf_dns_record_id == "dns2"


def test_cloud_infrastructure_names_are_reserved() -> None:
    """`edge` is the fallback origin every custom hostname routes through, and `console` is the
    instance console: an org taking either breaks the instance, not just itself."""
    for slug in ("edge", "console", "admin", "mx", "ns"):
        with pytest.raises(AppError) as caught:
            org_service.validate_slug(slug)
        assert caught.value.fields == {"slug": "errors.invalid_slug"}


async def test_self_host_provisioning_touches_no_dns(monkeypatch) -> None:
    fake = _FakeCloudflare()
    monkeypatch.setattr(settings, "cloud_cf_api_token", "tok")
    monkeypatch.setattr(settings, "cloud_cf_zone_id", "zone-123")
    monkeypatch.setattr(cf, "_transport", httpx.MockTransport(fake.handler))
    actor = await make_tenant("cf-sh-prov")
    org_id = await _make_org(actor.user.id, name="Zelf", slug="zelfhost")
    assert fake.calls == []
    async with async_session_maker() as session:
        assert (await session.get(Org, org_id)).cf_dns_record_id is None


# --------------------------------------------------------------------------- #
# Canonical host & custom-domain lifecycle (#291)
# --------------------------------------------------------------------------- #
@pytest.fixture
def dns_points(monkeypatch) -> dict:
    """Stand in for the DNS drift check; tests flip `value` between True/False/None."""
    state = {"value": True}

    async def fake_points_at(host: str, target: str) -> bool | None:
        return state["value"]

    from app.core import dnscheck as dnscheck_module

    monkeypatch.setattr(dnscheck_module, "points_at", fake_points_at)
    return state


async def _verified_tenant(client_for, cloudflare, published_txt, slug: str, domain: str):
    tenant = await make_tenant(slug)
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _claim_and_publish(client, headers, published_txt, domain)
        verified = await client.post("/api/v1/meta/tenant/domain/verify", headers=headers)
        assert verified.status_code == 200
    return tenant, headers


async def test_verify_is_not_activation(client_for, cloudflare, published_txt) -> None:
    """A fresh custom hostname answers "pending": the domain is verified (ownership proven,
    routed) but not live — nothing may present it as the org's address yet."""
    tenant, headers = await _verified_tenant(
        client_for, cloudflare, published_txt, "cf-notyet", "crm.wacht.test"
    )
    async with client_for(tenant.host) as client:
        status = (await client.get("/api/v1/meta/tenant/domain", headers=headers)).json()
        assert status["hostname_status"] == "pending"
        assert status["ssl_status"] == "pending_validation"
        assert status["live"] is False
        assert status["canonical_host"] == tenant.host  # the slug host stays canonical
        assert status["recovery_host"] == tenant.host

        branding = (await client.get("/api/v1/meta/tenant")).json()
        assert branding["canonical_host"] is None  # nothing redirects anywhere
        assert branding["domain_unhealthy"] is True


async def test_check_flips_the_canonical_host_when_all_three_are_ready(
    client_for, cloudflare, published_txt, dns_points
) -> None:
    tenant, headers = await _verified_tenant(
        client_for, cloudflare, published_txt, "cf-live", "crm.actief.test"
    )
    cloudflare.set_state(
        "ch1", status="active", ssl_status="active", expires_on="2027-01-15T00:00:00Z"
    )
    async with client_for(tenant.host) as client:
        checked = (
            await client.post("/api/v1/meta/tenant/domain/check", headers=headers)
        ).json()
        assert checked["hostname_status"] == "active"
        assert checked["ssl_status"] == "active"
        assert checked["dns_ok"] is True
        assert checked["live"] is True
        assert checked["canonical_host"] == "crm.actief.test"
        assert checked["cert_expires_at"].startswith("2027-01-15")

        # The slug host keeps resolving (recovery path) and now advertises the redirect.
        branding = (await client.get("/api/v1/meta/tenant")).json()
        assert branding["canonical_host"] == "crm.actief.test"
        assert branding["domain_unhealthy"] is False

    # On the canonical host itself the advertised host compares equal — the web hook
    # therefore never redirects there. One direction, one hop: no loop is constructible.
    async with client_for("crm.actief.test") as client:
        branding = (await client.get("/api/v1/meta/tenant")).json()
        assert branding["canonical_host"] == "crm.actief.test"


async def test_dns_moved_away_demotes_the_domain_and_alerts_nobody_twice(
    client_for, cloudflare, published_txt, dns_points, monkeypatch
) -> None:
    """The customer re-points their DNS: the domain stops being canonical, the slug host
    carries the org, and the daily sweep mails the domain managers exactly once."""
    tenant, headers = await _verified_tenant(
        client_for, cloudflare, published_txt, "cf-moved", "crm.verhuisd.test"
    )
    cloudflare.set_state("ch1", status="active", ssl_status="active")
    async with client_for(tenant.host) as client:
        assert (
            await client.post("/api/v1/meta/tenant/domain/check", headers=headers)
        ).json()["live"] is True

    dns_points["value"] = False
    async with client_for(tenant.host) as client:
        demoted = (
            await client.post("/api/v1/meta/tenant/domain/check", headers=headers)
        ).json()
        assert demoted["dns_ok"] is False
        assert demoted["live"] is False
        assert demoted["canonical_host"] == tenant.host
        branding = (await client.get("/api/v1/meta/tenant")).json()
        assert branding["canonical_host"] is None
        assert branding["domain_unhealthy"] is True

    # The sweep alerts once per distinct problem, and clears the slate on recovery.
    from app.core.cloud import domain_health

    sent: list[OutgoingEmail] = []

    async def fake_send(session, org_id, mail):  # noqa: ANN001
        sent.append(mail)
        return True, None

    monkeypatch.setattr(domain_health, "send_org_email", fake_send)
    async with async_session_maker() as session:
        first = await domain_health.sweep_domain_health(session)
        await session.commit()
    assert first["alerted"] == 1
    assert len(sent) == 1  # the tenant owner holds "*", so they are the domain manager
    assert tenant.user.email == sent[0].to

    async with async_session_maker() as session:
        second = await domain_health.sweep_domain_health(session)
        await session.commit()
    assert second["alerted"] == 0 and len(sent) == 1  # same problem, no repeat mail

    dns_points["value"] = True
    async with async_session_maker() as session:
        recovered = await domain_health.sweep_domain_health(session)
        await session.commit()
    assert recovered["alerted"] == 0
    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        assert org.domain_alerted_for is None  # a future problem alerts again


async def test_sweep_warns_ahead_of_a_failing_renewal(
    client_for, cloudflare, published_txt, dns_points, monkeypatch
) -> None:
    """Healthy statuses but an expiry closing in means HTTP DCV renewal is not happening —
    that is discovered here, not by browsers rejecting TLS."""
    tenant, headers = await _verified_tenant(
        client_for, cloudflare, published_txt, "cf-renew", "crm.bijna.test"
    )
    soon = (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cloudflare.set_state("ch1", status="active", ssl_status="active", expires_on=soon)

    from app.core.cloud import domain_health

    sent: list[OutgoingEmail] = []

    async def fake_send(session, org_id, mail):  # noqa: ANN001
        sent.append(mail)
        return True, None

    monkeypatch.setattr(domain_health, "send_org_email", fake_send)
    async with async_session_maker() as session:
        counts = await domain_health.sweep_domain_health(session)
        await session.commit()
    assert counts["alerted"] == 1

    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        assert org.domain_alerted_for.startswith("expiry:")
        # Still live: an expiring-but-valid certificate serves — the alert is the point.
        from app.core.hosts import custom_domain_live

        assert custom_domain_live(org) is True


async def test_hostname_deleted_behind_our_back_is_reported(
    client_for, cloudflare, published_txt, dns_points
) -> None:
    tenant, headers = await _verified_tenant(
        client_for, cloudflare, published_txt, "cf-gone", "crm.weg.test"
    )
    cloudflare.records.clear()
    async with client_for(tenant.host) as client:
        checked = (
            await client.post("/api/v1/meta/tenant/domain/check", headers=headers)
        ).json()
        assert checked["hostname_status"] == "deleted"
        assert checked["live"] is False
        assert checked["check_error"]


async def test_a_cloudflare_outage_is_not_a_state_change(
    client_for, cloudflare, published_txt, dns_points
) -> None:
    tenant, headers = await _verified_tenant(
        client_for, cloudflare, published_txt, "cf-blip", "crm.storing.test"
    )
    cloudflare.set_state("ch1", status="active", ssl_status="active")
    async with client_for(tenant.host) as client:
        await client.post("/api/v1/meta/tenant/domain/check", headers=headers)

        cloudflare.fail_with = [500, 500]
        blipped = (
            await client.post("/api/v1/meta/tenant/domain/check", headers=headers)
        ).json()
        # The previous statuses stand; only the error is recorded. Still live.
        assert blipped["hostname_status"] == "active"
        assert blipped["live"] is True
        assert blipped["check_error"]


async def test_clear_resets_the_lifecycle_state(
    client_for, cloudflare, published_txt, dns_points
) -> None:
    tenant, headers = await _verified_tenant(
        client_for, cloudflare, published_txt, "cf-reset", "crm.schoon.test"
    )
    async with client_for(tenant.host) as client:
        await client.post("/api/v1/meta/tenant/domain/check", headers=headers)
        cleared = (await client.delete("/api/v1/meta/tenant/domain", headers=headers)).json()
        assert cleared["hostname_status"] is None
        assert cleared["checked_at"] is None
        assert cleared["live"] is False
        assert cleared["canonical_host"] == tenant.host

    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        assert org.cf_hostname_status is None
        assert org.domain_checked_at is None
        assert org.domain_alerted_for is None


async def test_without_cloudflare_a_verified_domain_is_live_immediately(
    client_for, monkeypatch, published_txt
) -> None:
    """Self-host / Traefik posture: the router and its Let's Encrypt certificate follow the
    verification directly — there is no state to poll, so verified means live (#202)."""
    tenant = await make_tenant("cf-le")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _claim_and_publish(client, headers, published_txt, "crm.zelf291.test")
        await client.post("/api/v1/meta/tenant/domain/verify", headers=headers)
        status = (await client.get("/api/v1/meta/tenant/domain", headers=headers)).json()
        assert status["hostname_status"] is None
        assert status["live"] is True
        assert status["canonical_host"] == "crm.zelf291.test"
        branding = (await client.get("/api/v1/meta/tenant")).json()
        assert branding["canonical_host"] == "crm.zelf291.test"


async def test_a_pre_291_row_is_not_demoted_by_the_upgrade() -> None:
    """An org verified before lifecycle tracking existed has a hostname id but no captured
    state. It must stay live until the first sweep records the truth — an upgrade must never
    silently move a working custom domain back to the slug host."""
    from app.core.hosts import canonical_host, custom_domain_live

    tenant = await make_tenant("cf-legacy")
    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        org.custom_domain = "crm.legacy.test"
        org.custom_domain_verified_at = datetime.now(UTC)
        org.cf_hostname_id = "ch-old"
        assert custom_domain_live(org) is True
        assert canonical_host(org) == "crm.legacy.test"
        # …and the moment a check records a non-live state, the demotion is real.
        org.domain_checked_at = datetime.now(UTC)
        org.cf_hostname_status = "moved"
        assert custom_domain_live(org) is False
        assert canonical_host(org) == f"{org.slug}.{settings.base_domain}"


# --------------------------------------------------------------------------- #
# Secrets from files: SCHAKL_<SETTING>_FILE (the Docker secret convention)
# --------------------------------------------------------------------------- #
def test_any_setting_can_come_from_a_secret_file(monkeypatch, tmp_path) -> None:
    """Generic, not Cloudflare-only: a Docker secret is a file, so every sensitive setting
    must be readable from one or it cannot stay out of the stack definition."""
    from app.config import Settings

    token = tmp_path / "cf"
    token.write_text("  tok-from-secret\n")  # a mounted secret usually ends in a newline
    s3key = tmp_path / "s3"
    s3key.write_text("SCWACCESSKEY\n")
    monkeypatch.setenv("SCHAKL_CLOUD_CF_API_TOKEN_FILE", str(token))
    monkeypatch.setenv("SCHAKL_STORAGE_S3_ACCESS_KEY_ID_FILE", str(s3key))

    loaded = Settings()
    assert loaded.cloud_cf_api_token == "tok-from-secret"
    assert loaded.storage_s3_access_key_id == "SCWACCESSKEY"


def test_an_unreadable_or_empty_secret_file_refuses_the_boot(monkeypatch, tmp_path) -> None:
    """Falling back to the default would be invisible: an unset S3 key surfaces as a broken
    upload weeks later, not as a container that refuses to start."""
    from app.config import Settings

    monkeypatch.setenv("SCHAKL_STORAGE_S3_SECRET_ACCESS_KEY_FILE", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="cannot be read"):
        Settings()

    empty = tmp_path / "empty"
    empty.write_text("   \n")
    monkeypatch.setenv("SCHAKL_STORAGE_S3_SECRET_ACCESS_KEY_FILE", str(empty))
    with pytest.raises(ValueError, match="empty"):
        Settings()


def test_a_misspelled_secret_file_variable_refuses_the_boot(monkeypatch, tmp_path) -> None:
    """SCHAKL_STORAGE_S3_KEY_FILE is a typo for ..._ACCESS_KEY_ID_FILE, not a request to
    ignore it — and silently ignoring it is how a credential ends up unset in production."""
    from app.config import Settings

    secret = tmp_path / "s"
    secret.write_text("value\n")
    monkeypatch.setenv("SCHAKL_STORAGE_S3_KEY_FILE", str(secret))
    with pytest.raises(ValueError, match="does not name a setting"):
        Settings()


def test_an_explicit_value_wins_over_the_file(monkeypatch, tmp_path) -> None:
    """So a stale _FILE left behind in a stack cannot break a working deployment."""
    from app.config import Settings

    monkeypatch.setenv("SCHAKL_CLOUD_CF_API_TOKEN_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("SCHAKL_CLOUD_CF_API_TOKEN", "direct")
    assert Settings().cloud_cf_api_token == "direct"
