"""Entitlements / licensed modules (issue #137).

Covers the whole gate: offline signature verification, the license API (superuser-gated),
the enable-time 409, the read-only-after-expiry 402 on a licensed module's mutations, the
bootstrap grace window (the upgrade path), and the /mcp surface gate.

Tests sign with an **ephemeral** keypair and point ``settings.license_public_key`` at it —
no private key material exists in this repo, mirroring production where signing lives in the
private schakl-licensing CLI.
"""

from __future__ import annotations

import base64
import json
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select as sql_select
from sqlalchemy import text as sql_text

from app.config import settings
from app.core.auth.models import User
from app.core.entitlements.service import (
    LicenseError,
    invalidate_license_cache,
    verify_license,
)
from app.core.models import InstanceLicense
from app.db import async_session_maker
from tests.conftest import auth_cookie, make_tenant


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(
    private: Ed25519PrivateKey,
    *,
    modules: list[str],
    days: int = 365,
    grace_days: int = 14,
) -> str:
    """Mirror of the private CLI's canonical signing (sorted keys, compact separators)."""
    now = datetime.now(UTC)
    payload = {
        "schema": 1,
        "license_id": str(uuid_mod.uuid4()),
        "customer": "Testbureau",
        "plan": "pro",
        "modules": sorted(modules),
        "instance_id": None,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grace_days": grace_days,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"SCHAKL1.{_b64url(payload_bytes)}.{_b64url(private.sign(payload_bytes))}"


async def _reset_instance_license(*, grace_started_days_ago: int = 0) -> None:
    async with async_session_maker() as session:
        row = await session.get(InstanceLicense, 1)
        if row is None:
            row = InstanceLicense(id=1)
            session.add(row)
        row.license_text = None
        row.installed_at = None
        row.installed_by_email = None
        row.grace_started_at = datetime.now(UTC) - timedelta(days=grace_started_days_ago)
        await session.commit()
    invalidate_license_cache()


@pytest.fixture
async def license_key(monkeypatch) -> Ed25519PrivateKey:
    """Ephemeral signing key; the app verifies against its public half. Leaves the instance
    row in a fresh bootstrap state afterwards so the rest of the suite keeps writing."""
    private = Ed25519PrivateKey.generate()
    public_b64 = _b64url(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    monkeypatch.setattr(settings, "license_public_key", public_b64)
    await _reset_instance_license()
    yield private
    await _reset_instance_license()


async def _make_superuser(user_id) -> None:
    async with async_session_maker() as session:
        await session.execute(
            sql_text("UPDATE users SET is_superuser = true WHERE id = :id"),
            {"id": str(user_id)},
        )
        await session.commit()


def test_paid_module_set_is_pinned() -> None:
    """Which modules are paid is a product decision, not an accident of refactoring — pin the
    module→sku map so a sku is never silently dropped or added. Free stays: companies (the
    hub), contacts, tasks, notifications."""
    from app.core.entitlements.service import licensed_skus
    from app.registry import registry

    module_skus = {
        name: sku for name, sku in licensed_skus().items() if registry.get(name) is not None
    }
    assert module_skus == {
        "automation": "automation",
        "cloudflare": "cloudflare",
        "domains": "domains",
        "google": "google",
        # Google Ads is a premium capability *on top of* `marketing`, the same ladder
        # `reporting` sits on: a tenant can license the dashboards without buying the surface
        # that reads and writes a client's live advertising account.
        "google_ads": "google_ads",
        # A live GA4 read surface: seventeen operations against somebody else's Analytics
        # account, and its own MCP section. Licensed like `google_ads` and *not* folded into
        # `marketing` — the dashboard module reads a nightly aggregate, this answers the
        # property's own questions, and an agency may want either without the other. Nothing
        # here mutates, so the gate governs enabling it and never the reading of it.
        "google_analytics": "google_analytics",
        # Tag Manager writes to what runs on a client's website, which is the highest-consequence
        # thing this platform does to somebody else's property — licensed for the same reason
        # `google_ads` is, one rung further along the same ladder.
        "google_tag_manager": "google_tag_manager",
        "hosting": "hosting",
        "hr": "hr",
        "interactions": "interactions",
        "invoicing": "invoicing",
        "leave": "leave",
        "marketing": "marketing",
        # The payment provider (epic #269) is a paid integration like every other outside
        # connection; the free CRM core never collects money.
        "mollie": "mollie",
        "oxxa": "oxxa",
        # The client portal (#193, #296) is what an agency sells access to, not a property of
        # the address book — hence its own sku, and hence the locked invite control on an
        # instance that has not bought it.
        "portal": "portal",
        "projects": "projects",
        # #300: generating, narrating and sending the periodic client report is a premium
        # capability *on top of* `marketing` — a tenant can license the dashboards without
        # buying the documents, which is the product ladder rather than a technical split.
        "reporting": "reporting",
        # SnelStart (#377) is a licensed integration for the same reason `mollie` and `oxxa`
        # are: it is a credential and a conversation with somebody else's paid service, sitting
        # on top of `invoicing` rather than inside the free CRM core.
        "snelstart": "snelstart",
        "subscriptions": "subscriptions",
        "time": "time",
        # Timeon is a credential and a conversation with somebody else's time-registration
        # service — the `snelstart` bracket, one module over. A tenant licenses `time`
        # without ever having heard of it.
        "timeon": "timeon",
        # Landed on dev with a sku and without an entry here, which is precisely what this test
        # exists to catch — recorded rather than quietly widened.
        "uptime": "uptime",
        "websites": "websites",
        # WordPress is what a *website* has, the way `cloudflare` is what a domain has: its
        # table holds administrator Application Passwords, so it is admin-only and licensed
        # rather than folded into `websites`.
        "wordpress": "wordpress",
    }
    free = {m.name for m in registry.all() if m.sku is None}
    assert free == {"companies", "contacts", "tasks", "notifications"}


def test_verify_roundtrip_and_tamper(license_key: Ed25519PrivateKey) -> None:
    key_text = _sign(license_key, modules=["leave", "mcp"])
    info = verify_license(key_text, settings.license_public_key)
    assert info.modules == ("leave", "mcp")
    assert info.plan == "pro"

    prefix, payload_b64, sig_b64 = key_text.split(".")
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["modules"] = ["leave", "mcp", "everything"]
    forged = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    tampered = f"{prefix}.{_b64url(forged)}.{sig_b64}"
    with pytest.raises(LicenseError):
        verify_license(tampered, settings.license_public_key)
    with pytest.raises(LicenseError):
        verify_license("SCHAKL1.not.alicense", settings.license_public_key)


async def test_license_api_superuser_gated(client_for, license_key) -> None:
    t = await make_tenant("lic-api")
    await _make_superuser(t.user.id)
    other = await make_tenant("lic-api-member", role="member")
    key_text = _sign(license_key, modules=["leave"])

    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        r = await c.get("/api/v1/instance/license", headers=headers)
        assert r.status_code == 200 and r.json()["installed"] is False

        r = await c.put("/api/v1/instance/license", json={"key": key_text}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["installed"] is True and body["modules"] == ["leave"]
        leave_state = next(s for s in body["licensed"] if s["sku"] == "leave")
        assert leave_state["entitled"] is True and leave_state["writable"] is True

        r = await c.put("/api/v1/instance/license", json={"key": "SCHAKL1.b.c"}, headers=headers)
        assert r.status_code == 422
        assert r.json()["error"]["message"] == "errors.license_invalid"

    async with client_for(other.host) as c:
        headers = await auth_cookie(other.user)
        assert (await c.get("/api/v1/instance/license", headers=headers)).status_code == 403
        r = await c.put("/api/v1/instance/license", json={"key": key_text}, headers=headers)
        assert r.status_code == 403


async def test_enabling_licensed_module_requires_license(client_for, license_key) -> None:
    t = await make_tenant("lic-enable")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        current = (await c.get("/api/v1/meta/tenant")).json()["enabled_modules"]

        # Dropping leave is always allowed…
        without = [m for m in current if m != "leave"]
        r = await c.patch(
            "/api/v1/meta/tenant", json={"enabled_modules": without}, headers=headers
        )
        assert r.status_code == 200, r.text

        # …and re-enabling it works inside the bootstrap/trial window (fresh installs)…
        r = await c.patch(
            "/api/v1/meta/tenant", json={"enabled_modules": current}, headers=headers
        )
        assert r.status_code == 200, r.text
        r = await c.patch(
            "/api/v1/meta/tenant", json={"enabled_modules": without}, headers=headers
        )
        assert r.status_code == 200, r.text

        # …but once that window is over, enabling without a license is the gate (#137).
        await _reset_instance_license(grace_started_days_ago=999)
        r = await c.patch(
            "/api/v1/meta/tenant", json={"enabled_modules": current}, headers=headers
        )
        assert r.status_code == 409, r.text
        assert r.json()["error"]["message"] == "errors.license_required"

        # With a covering license installed, the same request succeeds.
        await _make_superuser(t.user.id)
        key_text = _sign(license_key, modules=["leave", "mcp"])
        r = await c.put("/api/v1/instance/license", json={"key": key_text}, headers=headers)
        assert r.status_code == 200, r.text
        r = await c.patch(
            "/api/v1/meta/tenant", json={"enabled_modules": current}, headers=headers
        )
        assert r.status_code == 200, r.text


async def test_expired_license_makes_module_read_only(client_for, license_key) -> None:
    t = await make_tenant("lic-expired")
    await _make_superuser(t.user.id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        # Valid license first: mutations work.
        good = _sign(license_key, modules=["leave"])
        assert (
            await c.put("/api/v1/instance/license", json={"key": good}, headers=headers)
        ).status_code == 200
        r = await c.post(
            "/api/v1/leave/types",
            json={"key": "vacation", "label_i18n": {"nl": "Vakantie", "en": "Vacation"}},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        # Expired past its grace window: mutations 402, reads keep working.
        expired = _sign(license_key, modules=["leave"], days=-30, grace_days=7)
        assert (
            await c.put("/api/v1/instance/license", json={"key": expired}, headers=headers)
        ).status_code == 200
        r = await c.post(
            "/api/v1/leave/types",
            json={"key": "extra", "label_i18n": {"nl": "Extra", "en": "Extra"}},
            headers=headers,
        )
        assert r.status_code == 402, r.text
        assert r.json()["error"]["message"] == "errors.license_expired"
        r = await c.get("/api/v1/leave/types", headers=headers)
        assert r.status_code == 200, r.text
        # The license endpoint itself never blocks — that is how you fix the situation.
        assert (
            await c.get("/api/v1/instance/license", headers=headers)
        ).status_code == 200


async def test_bootstrap_grace_window(client_for, license_key) -> None:
    """The upgrade path: leave enabled before licensing shipped keeps writing during the
    bootstrap window, then turns read-only — reads stay."""
    t = await make_tenant("lic-bootstrap")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        r = await c.post(
            "/api/v1/leave/types",
            json={"key": "vacation", "label_i18n": {"nl": "Vakantie", "en": "Vacation"}},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        await _reset_instance_license(grace_started_days_ago=999)
        r = await c.post(
            "/api/v1/leave/types",
            json={"key": "late", "label_i18n": {"nl": "Laat", "en": "Late"}},
            headers=headers,
        )
        assert r.status_code == 402, r.text
        assert (await c.get("/api/v1/leave/types", headers=headers)).status_code == 200


async def test_mcp_surface_gated(client_for, license_key) -> None:
    t = await make_tenant("lic-mcp")
    async with client_for(t.host) as c:
        await _reset_instance_license(grace_started_days_ago=999)
        r = await c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 402, r.text
        assert r.json()["error"]["message"] == "errors.license_expired"


async def test_portal_invites_are_licensed_but_the_way_out_is_not(
    client_for, license_key
) -> None:
    """The portal's write gate, and the one route that must survive it (#296).

    An unlicensed instance may not invite a new client — that is the whole point of the sku,
    and it is what the web renders as a locked control. But ending an impersonation is not a
    purchase: gating it would strand whoever was inside a client's session the moment the
    licence lapsed, with no way out. ``license_exempt`` is what separates the two, and this
    pins both halves — a gate that quietly covered everything would look identical until
    somebody was trapped.
    """
    t = await make_tenant("lic-portal")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": "piet-lic@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        subject = f"/api/v1/portal/logins/contact/{contact['id']}"

        # Inside the bootstrap window an unlicensed install still works (the built-in trial).
        assert (await c.post(subject, headers=headers)).status_code == 200
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                sql_select(User).where(User.email == "piet-lic@example.com")
            )
        assert portal_user is not None
        portal_headers = await auth_cookie(portal_user)

        await _reset_instance_license(grace_started_days_ago=999)
        # Past it: no new invite goes out…
        r = await c.post(subject, headers=headers)
        assert r.status_code == 402, r.text
        assert r.json()["error"]["message"] == "errors.license_expired"
        # …but reading the state keeps working, so the screen can explain itself rather than
        # erroring, and existing logins are never revoked by a licence lapsing.
        state = await c.get(subject, headers=headers)
        assert state.status_code == 200, state.text
        assert state.json()["status"] in {"invited", "active"}
        # …and the escape hatch answers whatever the licence says.
        assert (
            await c.post("/api/v1/portal/impersonation/stop", headers=headers)
        ).status_code == 204

        # The client whose login already exists still signs in, and is still *contained*: the
        # horizon and the "is this a portal login" resolver live in contacts precisely so a
        # lapsed licence can never widen what an existing client sees. Locking the invite is a
        # commercial decision; un-scoping a live session would be a security incident.
        me = await c.get("/api/v1/meta/me", headers=portal_headers)
        assert me.status_code == 200, me.text
        assert me.json()["is_portal"] is True
        theirs = await c.get("/api/v1/companies", headers=portal_headers)
        assert [row["id"] for row in theirs.json()["items"]] == [company["id"]]
