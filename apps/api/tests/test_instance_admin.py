"""Instance administration (issue #26): setup wizard, org lifecycle, impersonation,
domain verification, export/import — including the gates that keep it all shut by default."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.core.models import InstanceAuditLog, Org
from app.db import async_session_maker, set_current_org
from tests.conftest import Tenant, add_membership, auth_cookie, make_tenant


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
async def start_handoff(
    client_for, admin: Tenant, target_org_id, target_user_id, *, minutes: int = 30
) -> dict:
    """Ask for a crossing from the admin's own host and return the handoff payload (#288)."""
    async with client_for(admin.host) as client:
        response = await client.post(
            f"/api/v1/instance/orgs/{target_org_id}/impersonate",
            json={"user_id": str(target_user_id), "minutes": minutes},
            headers=await auth_cookie(admin.user),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    # Crossing hosts, so no usable grant may come back here — only a ticket for the other host.
    assert body["token"] is None
    assert body["handoff"] is not None
    return body["handoff"]


async def claim(client_for, host: str, ticket: str):
    async with client_for(host) as client:
        return await client.post(
            "/api/v1/instance/impersonation/claim", json={"ticket": ticket}
        )


async def test_impersonation_is_time_boxed_audited_and_visible(
    client_for, instance_admin_enabled
) -> None:
    admin = await make_tenant("imp-admin")
    await make_instance_owner(admin)
    admin_headers = await auth_cookie(admin.user)
    target = await make_tenant("imp-target", email="member@example.org", role="member")

    # The console runs on its own host, so the crossing is a handoff (#288).
    handoff = await start_handoff(
        client_for, admin, target.org.id, target.user.id, minutes=9999
    )
    assert handoff["host"] == target.host

    redeemed = await claim(client_for, target.host, handoff["ticket"])
    assert redeemed.status_code == 200, redeemed.text
    body = redeemed.json()
    token = body["token"]
    session_token = body["session_token"]

    # Clamped to the configured maximum (60 min) — and the admin's session on this host is
    # minted to lapse with the grant, never for the ordinary week.
    from datetime import UTC, datetime, timedelta

    expires_at = datetime.fromisoformat(body["expires_at"])
    max_allowed = timedelta(minutes=settings.impersonation_max_minutes + 1)
    assert expires_at <= datetime.now(UTC) + max_allowed
    assert 0 < body["max_age"] <= (settings.impersonation_max_minutes + 1) * 60
    assert body["session_cookie"] == settings.auth_cookie_name

    both_cookies = {"Cookie": f"schakl_auth={session_token}; schakl_impersonate={token}"}

    # On the target org's host the admin now *is* the member — visibly.
    async with client_for(target.host) as client:
        me = await client.get("/api/v1/meta/me", headers=both_cookies)
        assert me.status_code == 200
        assert me.json()["email"] == "member@example.org"
        assert me.json()["impersonated_by"] == admin.user.email
        assert me.json()["impersonation_expires_at"] is not None
        assert me.json()["is_instance_admin"] is False  # effective user, not the admin

    # Without the grant cookie the admin has nothing here. Their *console* session is not a
    # session on this host at all (a session names its org — CLAUDE.md §5), which is why the
    # handoff has to mint a second one; that 401 is what makes the grant load-bearing.
    async with client_for(target.host) as client:
        assert (await client.get("/api/v1/meta/me", headers=admin_headers)).status_code == 401

    # A session belonging to someone else cannot activate the grant. Deliberately someone with
    # a *valid* session on this very host — a colleague in the target org — so the refusal is
    # the grant's own "names another admin" rule, not the host boundary standing in for it.
    other = await make_tenant("imp-bystander")
    async with async_session_maker() as session:
        await set_current_org(session, target.org.id)
        await add_membership(session, target.org.id, other.user.id, role="member")
        await session.commit()
    other_cookie = await auth_cookie(other.user, org_id=target.org.id)
    hijack = {"Cookie": f"{other_cookie['Cookie']}; schakl_impersonate={token}"}
    async with client_for(target.host) as client:
        stolen = await client.get("/api/v1/meta/me", headers=hijack)
        assert stolen.status_code == 200
        # Still themselves: the grant is ignored, never applied to whoever presents it.
        assert stolen.json()["email"] == other.user.email
        assert stolen.json()["impersonated_by"] is None

    # …and neither does the grant on its own: it authenticates nobody (#288's promise that a
    # stolen grant stays insufficient).
    async with client_for(target.host) as client:
        alone = await client.get(
            "/api/v1/meta/me", headers={"Cookie": f"schakl_impersonate={token}"}
        )
        assert alone.status_code == 401

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
    assert "impersonate.handoff" in actions
    assert "impersonate.start" in actions and "impersonate.stop" in actions


async def test_a_handoff_ticket_is_single_use(client_for, instance_admin_enabled) -> None:
    admin = await make_tenant("hand-once-admin")
    await make_instance_owner(admin)
    target = await make_tenant("hand-once", email="once@example.org", role="member")

    handoff = await start_handoff(client_for, admin, target.org.id, target.user.id)
    assert (await claim(client_for, target.host, handoff["ticket"])).status_code == 200

    # Replaying the link — browser history, a proxy log, a screen share — opens nothing.
    replayed = await claim(client_for, target.host, handoff["ticket"])
    assert replayed.status_code == 403
    assert replayed.json()["error"]["message"] == "errors.impersonation_handoff_invalid"


async def test_a_handoff_ticket_only_works_on_the_host_it_names(
    client_for, instance_admin_enabled
) -> None:
    admin = await make_tenant("hand-host-admin")
    await make_instance_owner(admin)
    target = await make_tenant("hand-host", email="host@example.org", role="member")
    bystander = await make_tenant("hand-host-other")

    handoff = await start_handoff(client_for, admin, target.org.id, target.user.id)

    # Presented anywhere else it is refused — including on the console's own host, which is
    # where a leaked URL would most plausibly be re-opened.
    for host in (bystander.host, admin.host):
        wrong = await claim(client_for, host, handoff["ticket"])
        assert wrong.status_code == 403, host
        assert wrong.json()["error"]["message"] == "errors.impersonation_handoff_invalid"

    # A refusal elsewhere must not burn the ticket: the operator's own tab still works.
    assert (await claim(client_for, target.host, handoff["ticket"])).status_code == 200


async def test_an_expired_handoff_ticket_is_refused(client_for, instance_admin_enabled) -> None:
    from datetime import UTC, datetime, timedelta

    from app.core.instance.impersonation import ImpersonationHandoff

    admin = await make_tenant("hand-exp-admin")
    await make_instance_owner(admin)
    target = await make_tenant("hand-exp", email="exp@example.org", role="member")

    handoff = await start_handoff(client_for, admin, target.org.id, target.user.id)
    async with async_session_maker() as session:
        row = await session.scalar(select(ImpersonationHandoff))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    lapsed = await claim(client_for, target.host, handoff["ticket"])
    assert lapsed.status_code == 403
    assert lapsed.json()["error"]["message"] == "errors.impersonation_handoff_invalid"


async def test_a_garbled_or_absent_ticket_refuses_rather_than_422(
    client_for, instance_admin_enabled
) -> None:
    """The one session-less route on this surface still refuses like the rest of it."""
    tenant = await make_tenant("hand-garbled")
    for payload in ({}, {"ticket": ""}, {"ticket": "not-a-ticket"}):
        async with client_for(tenant.host) as client:
            response = await client.post(
                "/api/v1/instance/impersonation/claim", json=payload
            )
        assert response.status_code == 403, payload


async def test_a_handoff_is_refused_once_the_capability_is_gone(
    client_for, instance_admin_enabled
) -> None:
    """A crossing that has not happened yet is re-authorized, unlike a grant already in flight
    (docs/CLOUD.md): withdrawing ``instance.impersonate`` stops the pending link dead."""
    from app.core.instance import capabilities as caps
    from app.core.models import InstanceAdmin

    admin = await make_tenant("hand-revoked-admin")
    async with async_session_maker() as session:
        session.add(
            InstanceAdmin(
                user_id=admin.user.id,
                capabilities=[caps.ORGS_READ, caps.IMPERSONATE],
                granted_by_email="owner@example.com",
            )
        )
        await session.commit()
    target = await make_tenant("hand-revoked", email="revoked@example.org", role="member")

    handoff = await start_handoff(client_for, admin, target.org.id, target.user.id)

    async with async_session_maker() as session:
        row = await session.scalar(
            select(InstanceAdmin).where(InstanceAdmin.user_id == admin.user.id)
        )
        row.capabilities = [caps.ORGS_READ]
        await session.commit()

    refused = await claim(client_for, target.host, handoff["ticket"])
    assert refused.status_code == 403
    assert refused.json()["error"]["message"] == "errors.impersonation_handoff_invalid"


async def test_impersonation_crosses_to_a_verified_custom_domain(
    client_for, instance_admin_enabled
) -> None:
    """The bug in #288: an org on its own domain is exactly where cookies cannot be shared."""
    from datetime import UTC, datetime

    admin = await make_tenant("hand-cd-admin")
    await make_instance_owner(admin)
    target = await make_tenant("hand-cd", email="cd@example.org", role="member")
    domain = "support.klant-eigen-domein.example"
    async with async_session_maker() as session:
        org = await session.get(Org, target.org.id)
        org.custom_domain = domain
        org.custom_domain_verified_at = datetime.now(UTC)
        await session.commit()

    handoff = await start_handoff(client_for, admin, target.org.id, target.user.id)
    # The handoff addresses the org the way hostname resolution does — the custom domain wins.
    assert handoff["host"] == domain

    redeemed = await claim(client_for, domain, handoff["ticket"])
    assert redeemed.status_code == 200, redeemed.text
    body = redeemed.json()
    cookies = {
        "Cookie": f"schakl_auth={body['session_token']}; schakl_impersonate={body['token']}"
    }
    async with client_for(domain) as client:
        me = await client.get("/api/v1/meta/me", headers=cookies)
        assert me.status_code == 200
        assert me.json()["email"] == "cd@example.org"
        assert me.json()["impersonated_by"] == admin.user.email

    # The org's slug host still resolves to the same org, and the ticket was for the domain.
    assert (await claim(client_for, target.host, handoff["ticket"])).status_code == 403


async def test_same_host_impersonation_still_sets_the_cookie_directly(
    client_for, instance_admin_enabled
) -> None:
    """A self-hosted box administering its own org shares one hostname, so there is nothing to
    hand off: the grant comes straight back and no ticket is minted."""
    admin = await make_tenant("imp-samehost")
    await make_instance_owner(admin)
    async with async_session_maker() as session:
        member = User(
            id=uuid.uuid4(),
            email="samehost-member@example.org",
            hashed_password="",
            is_active=True,
            is_verified=True,
        )
        session.add(member)
        await session.flush()
        await set_current_org(session, admin.org.id)
        await add_membership(session, admin.org.id, member.id, "member")
        await session.commit()
        member_id = member.id

    async with client_for(admin.host) as client:
        response = await client.post(
            f"/api/v1/instance/orgs/{admin.org.id}/impersonate",
            json={"user_id": str(member_id), "minutes": 15},
            headers=await auth_cookie(admin.user),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["handoff"] is None
    assert body["token"]

    cookies = {
        "Cookie": f"{(await auth_cookie(admin.user))['Cookie']}; "
        f"schakl_impersonate={body['token']}"
    }
    async with client_for(admin.host) as client:
        me = await client.get("/api/v1/meta/me", headers=cookies)
        assert me.status_code == 200
        assert me.json()["email"] == "samehost-member@example.org"


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

    # The exported owner (matched by email) can use the imported org; FKs were remapped. Their
    # session there is its own: a session names the org it was minted for (CLAUDE.md §5), and an
    # import creates a *new* org that no existing session can already belong to.
    copy_headers = await auth_cookie(source.user, org_id=uuid.UUID(body["org"]["id"]))
    async with client_for("port-copy.localhost") as client:
        companies = await client.get("/api/v1/companies", headers=copy_headers)
        assert companies.status_code == 200
        assert companies.json()["total"] == 1
        copied = companies.json()["items"][0]
        assert copied["name"] == "Rondreis BV"
        assert copied["id"] != company_id  # fresh primary keys

        contacts = await client.get(
            "/api/v1/contacts", params={"company_id": copied["id"]}, headers=copy_headers
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
async def test_domain_claim_check_and_uniqueness(client_for, fake_dns) -> None:
    a = await make_tenant("dom-a")
    b = await make_tenant("dom-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

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
        status = claimed.json()
        assert status["stage"] == "ownership_pending"
        card = next(r for r in status["records"] if r["purpose"] == "ownership")
        assert card["name"] == "_schakl-challenge.crm.agency.test"
        token = card["value"]

        # NXDOMAIN reads as propagation (pending), SERVFAIL as a broken zone (failed) —
        # the distinct diagnoses #292 requires instead of one generic failure.
        from app.core import dnscheck

        fake_dns.txt["_schakl-challenge.crm.agency.test"] = dnscheck.NXDOMAIN
        report = (
            await client.post("/api/v1/meta/tenant/domain/check", headers=a_headers)
        ).json()
        assert (report["checks"][0]["state"], report["checks"][0]["code"]) == (
            "pending", "txt_nxdomain",
        )
        fake_dns.txt["_schakl-challenge.crm.agency.test"] = dnscheck.SERVFAIL
        report = (
            await client.post("/api/v1/meta/tenant/domain/check", headers=a_headers)
        ).json()
        assert (report["checks"][0]["state"], report["checks"][0]["code"]) == (
            "failed", "dns_servfail",
        )

        fake_dns.txt["_schakl-challenge.crm.agency.test"] = ["something-else", token]
        report = (
            await client.post("/api/v1/meta/tenant/domain/check", headers=a_headers)
        ).json()
        # Self-host: ownership is the whole story — one good check activates.
        assert report["advanced"] is True
        assert report["status"]["custom_domain"] == "crm.agency.test"
        assert report["status"]["pending_domain"] is None

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


async def test_instance_admin_configures_domain_directly(
    client_for, fake_dns, instance_admin_enabled
) -> None:
    """The operator path (#292): set a custom domain on an org from the instance surface —
    operator-asserted ownership skips the TXT challenge and is audited as such."""
    admin = await make_tenant("dom-admin")
    await make_instance_owner(admin)
    target = await make_tenant("dom-target")
    other = await make_tenant("dom-other")
    headers = await auth_cookie(admin.user)

    async with client_for(admin.host) as client:
        set_resp = await client.put(
            f"/api/v1/instance/orgs/{target.org.id}/domain",
            json={"domain": "Portaal.Klant.example."},
            headers=headers,
        )
        assert set_resp.status_code == 200
        status = set_resp.json()
        # Normalized, active immediately, no ownership challenge issued.
        assert status["custom_domain"] == "portaal.klant.example"
        assert status["stage"] == "active"
        assert status["pending_domain"] is None

        # Global uniqueness holds on the operator path too.
        conflict = await client.put(
            f"/api/v1/instance/orgs/{other.org.id}/domain",
            json={"domain": "portaal.klant.example"},
            headers=headers,
        )
        assert conflict.status_code == 409

        # claim mode only reserves + issues the challenge; the org admin finishes the wizard.
        claim_resp = await client.put(
            f"/api/v1/instance/orgs/{other.org.id}/domain",
            json={"domain": "crm.ander.example", "mode": "claim"},
            headers=headers,
        )
        assert claim_resp.status_code == 200
        assert claim_resp.json()["stage"] == "ownership_pending"
        assert claim_resp.json()["custom_domain"] is None

        read_back = await client.get(
            f"/api/v1/instance/orgs/{other.org.id}/domain", headers=headers
        )
        assert read_back.json()["pending_domain"] == "crm.ander.example"

        cleared = await client.delete(
            f"/api/v1/instance/orgs/{target.org.id}/domain", headers=headers
        )
        assert cleared.status_code == 200
        assert cleared.json()["stage"] == "none"

    # The audit trail records the operator assertion.
    async with async_session_maker() as session:
        actions = (
            (
                await session.execute(
                    select(InstanceAuditLog.action, InstanceAuditLog.detail).order_by(
                        InstanceAuditLog.created_at.asc()
                    )
                )
            )
            .all()
        )
        attach = next(row for row in actions if row[0] == "domain.attach")
        assert attach[1]["ownership"] == "operator-asserted"


async def test_instance_org_create_with_custom_domain(
    client_for, fake_dns, instance_admin_enabled
) -> None:
    admin = await make_tenant("dom-create-admin")
    await make_instance_owner(admin)
    headers = await auth_cookie(admin.user)
    async with client_for(admin.host) as client:
        created = await client.post(
            "/api/v1/instance/orgs",
            json={
                "name": "Klant BV",
                "slug": "klantbv-dom",
                "custom_domain": "app.klantbv.example",
            },
            headers=headers,
        )
        assert created.status_code == 201
        assert created.json()["custom_domain"] == "app.klantbv.example"
        assert created.json()["custom_domain_verified"] is True

    # The domain resolves to the new org straight away (DNS willing).
    async with client_for("app.klantbv.example") as client:
        assert (await client.get("/api/v1/meta/tenant")).json()["slug"] == "klantbv-dom"
