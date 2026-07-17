"""Adversarial security tests authored during the branch security audit.

Every test here is attacker-shaped: a *low-privileged member of tenant A* trying to cross into
tenant B, or to escalate privilege inside A. They drive the full HTTP → tenancy → RLS → RBAC
stack (real Postgres, non-superuser role, FORCE RLS), so a green run is positive evidence the
control holds — not a mock.

Two tests are deliberately *demonstrations of a finding* (prefixed ``test_FINDING_``): they
assert that the weakness is real. See SECURITY_AUDIT.md for the write-ups.
"""

from __future__ import annotations

import uuid

from fastapi_users.jwt import generate_jwt

from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member


async def _create_company(client, headers, name: str = "Acme") -> str:
    r = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --------------------------------------------------------------------------- #
# Tenant isolation — the crown jewel
# --------------------------------------------------------------------------- #
async def test_cross_tenant_company_crud_is_404_every_verb(client_for) -> None:
    """A member of B cannot read, mutate, or delete A's company by id — existence stays hidden."""
    a = await make_tenant("adv-iso-a")
    b = await make_tenant("adv-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        a_company = await _create_company(ca, a_headers, "A Secret Corp")

    async with client_for(b.host) as cb:
        got = await cb.get(f"/api/v1/companies/{a_company}", headers=b_headers)
        assert got.status_code == 404
        assert (
            await cb.patch(
                f"/api/v1/companies/{a_company}", json={"name": "pwned"}, headers=b_headers
            )
        ).status_code == 404
        assert (
            await cb.delete(f"/api/v1/companies/{a_company}", headers=b_headers)
        ).status_code == 404

    # And A's row is untouched.
    async with client_for(a.host) as ca:
        got = await ca.get(f"/api/v1/companies/{a_company}", headers=a_headers)
        assert got.status_code == 200
        assert got.json()["name"] == "A Secret Corp"


async def test_valid_session_on_foreign_host_is_forbidden(client_for) -> None:
    """A's genuine cookie replayed against B's hostname → 403 (A is not a member of B)."""
    a = await make_tenant("adv-host-a")
    await make_tenant("adv-host-b")  # b exists as a distinct tenant/host
    a_headers = await auth_cookie(a.user)
    async with client_for("adv-host-b.localhost") as c:
        r = await c.get("/api/v1/companies", headers=a_headers)
        assert r.status_code == 403


async def test_org_id_in_body_cannot_reassign_tenant(client_for) -> None:
    """Mass-assignment guard: a client-supplied ``org_id``/``id`` in the body is ignored; the row
    belongs to the caller's tenant and the target tenant never sees it."""
    a = await make_tenant("adv-mass-a")
    b = await make_tenant("adv-mass-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        r = await ca.post(
            "/api/v1/companies",
            json={
                "name": "Planted",
                "org_id": str(b.org.id),          # attacker tries to plant it in B
                "id": str(uuid.uuid4()),           # and to choose the id
            },
            headers=a_headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["org_id"] == str(a.org.id), "org_id must come from context, not the body"

    async with client_for(b.host) as cb:
        listing = await cb.get("/api/v1/companies", headers=b_headers)
        assert listing.json()["total"] == 0, "row must not have landed in tenant B"


# --------------------------------------------------------------------------- #
# Privilege escalation within a tenant
# --------------------------------------------------------------------------- #
async def test_member_cannot_touch_role_admin_endpoints(client_for) -> None:
    """A plain member holds neither settings.roles.manage nor members.member.write, so every
    role/permission-administration route denies before the handler runs."""
    tenant = await make_tenant("adv-esc")
    member = await add_member(tenant, role="member")
    m_headers = await auth_cookie(member)
    rid = str(uuid.uuid4())
    mid = str(uuid.uuid4())

    async with client_for(tenant.host) as c:
        # Create a role (would let them mint capabilities)
        assert (
            await c.post(
                "/api/v1/roles",
                json={
                    "key": "pwn",
                    "name_i18n": {"en": "pwn"},
                    "permissions": ["companies.company.read"],
                },
                headers=m_headers,
            )
        ).status_code == 403
        # Edit an existing role's permissions
        assert (
            await c.patch(
                f"/api/v1/roles/{rid}", json={"permissions": ["*"]}, headers=m_headers
            )
        ).status_code == 403
        # Assign themselves a different system role (route is PATCH /members/{id})
        assert (
            await c.patch(
                f"/api/v1/members/{mid}", json={"role": "admin"}, headers=m_headers
            )
        ).status_code == 403
        # Replace their whole role set
        assert (
            await c.put(
                f"/api/v1/members/{mid}/roles", json={"role_ids": [rid]}, headers=m_headers
            )
        ).status_code == 403


async def test_wildcard_permission_is_not_assignable(client_for) -> None:
    """Even an admin holding settings.roles.manage cannot mint the ``*`` wildcard onto a role —
    it belongs to the owner system role alone (validate_permissions rejects it)."""
    tenant = await make_tenant("adv-wildcard")  # owner holds settings.roles.manage
    owner_headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as c:
        r = await c.post(
            "/api/v1/roles",
            json={"key": "superperms", "name_i18n": {"en": "x"}, "permissions": ["*"]},
            headers=owner_headers,
        )
        assert r.status_code == 422, r.text


async def test_cross_tenant_role_access_is_404(client_for) -> None:
    """Owner of A cannot read or mutate B's role by id — role administration is org-scoped."""
    a = await make_tenant("adv-role-a")
    b = await make_tenant("adv-role-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(b.host) as cb:
        b_roles = (await cb.get("/api/v1/roles", headers=b_headers)).json()
        b_role_id = b_roles[0]["id"]

    async with client_for(a.host) as ca:
        got = await ca.get(f"/api/v1/roles/{b_role_id}", headers=a_headers)
        assert got.status_code in (404, 405)
        assert (
            await ca.patch(
                f"/api/v1/roles/{b_role_id}", json={"permissions": []}, headers=a_headers
            )
        ).status_code == 404
        assert (
            await ca.delete(f"/api/v1/roles/{b_role_id}", headers=a_headers)
        ).status_code == 404


# --------------------------------------------------------------------------- #
# FINDING demonstrations (these assert the weakness IS real)
# --------------------------------------------------------------------------- #
async def test_FINDING_forged_session_with_public_default_secret(client_for) -> None:
    """C1: the signing secret defaults to a literal that ships in the public repo, and nothing
    refuses to boot on it. An attacker who knows that string forges a session for any user id
    with no login. This test forges a cookie for tenant A's owner using ONLY the public default
    string and confirms the API accepts it.

    The fix (a production boot guard, applied in this branch) prevents an instance from *running*
    on the default secret; it does not change this dev-mode behaviour, so this test stays valid as
    documentation of the pre-fix exposure.
    """
    a = await make_tenant("adv-forge")
    # The old default from config.py / .env.example — publicly known, must never verify.
    public_default_secret = "change-me-in-production-please-32bytes-min"
    forged = generate_jwt(
        {"sub": str(a.user.id), "aud": ["fastapi-users:auth"]},
        public_default_secret,
        3600,
    )
    async with client_for(a.host) as c:
        r = await c.get("/api/v1/companies", headers={"Cookie": f"schakl_auth={forged}"})
        assert r.status_code == 200, (
            "forged token built from the public default secret was accepted"
        )


async def test_FINDING_member_directory_exposed_to_every_member(client_for) -> None:
    """The /members/lookup endpoint is no_permission_required, so any member (or a forged token)
    can enumerate every colleague's user_id + email — including the superuser instance owner —
    which is the UUID an attacker needs to target the C1 forgery precisely."""
    tenant = await make_tenant("adv-dir")
    member = await add_member(tenant, role="member")
    m_headers = await auth_cookie(member)
    async with client_for(tenant.host) as c:
        r = await c.get("/api/v1/members/lookup", headers=m_headers)
        assert r.status_code == 200
        emails = {row["email"] for row in r.json()}
        assert tenant.user.email in emails, "a plain member can read the owner's email + user_id"


# --------------------------------------------------------------------------- #
# Fix verification (audit F2 / C2 / web-XSS) — these pass BECAUSE the fix is applied
# --------------------------------------------------------------------------- #
async def _membership_id_of(client, owner_headers, email: str) -> str:
    members = (await client.get("/api/v1/members", headers=owner_headers)).json()
    return next(m["membership_id"] for m in members if m["email"] == email)


async def test_FIXED_members_write_cannot_escalate_to_owner(client_for) -> None:
    """F2: a holder of members.member.write (a team-management capability) must NOT be able to
    confer the wildcard-bearing ``owner`` role — not on themselves via role-swap, nor via invite.
    Before the fix both returned success; the guard now denies with 403."""
    tenant = await make_tenant("adv-esc-owner")
    owner_headers = await auth_cookie(tenant.user)
    member = await add_member(tenant, role="member")
    m_headers = await auth_cookie(member)

    async with client_for(tenant.host) as c:
        # Owner mints a custom role carrying only members.member.write and assigns it to the member.
        role = await c.post(
            "/api/v1/roles",
            json={"key": "officemgr", "name_i18n": {"en": "Office"},
                  "permissions": ["members.member.write", "members.member.read"]},
            headers=owner_headers,
        )
        assert role.status_code == 201, role.text
        custom_role_id = role.json()["id"]
        member_mid = await _membership_id_of(c, owner_headers, member.email)
        assigned = await c.put(
            f"/api/v1/members/{member_mid}/roles",
            json={"role_ids": [custom_role_id]}, headers=owner_headers,
        )
        assert assigned.status_code == 200, assigned.text

        # The member now holds members.member.write. Try to self-promote to owner → must be 403.
        promote = await c.patch(
            f"/api/v1/members/{member_mid}", json={"role": "owner"}, headers=m_headers
        )
        assert promote.status_code == 403, "members.member.write must not grant owner/*"

        # And they cannot mint a brand-new owner via invite either.
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "accomplice@example.com", "role": "owner"}, headers=m_headers,
        )
        assert invited.status_code == 403

        # Sanity: assigning a *non-owner* role via the same capability still works.
        ok = await c.patch(
            f"/api/v1/members/{member_mid}", json={"role": "admin"}, headers=m_headers
        )
        assert ok.status_code == 200, ok.text


async def test_FIXED_oidc_requires_verified_email_to_adopt_account(
    client_for, monkeypatch
) -> None:
    """C2: SSO must not adopt a *pre-existing* local account on a bare, unverified email claim
    (account takeover, incl. the /setup owner). Unverified → refused; verified → allowed."""
    from app.core.auth import sso
    from tests.test_auth_oidc_gate import _configure, _StubClient

    tenant = await make_tenant("adv-oidc-takeover")  # tenant.user is a pre-existing local account
    headers = await auth_cookie(tenant.user)
    victim = tenant.user.email

    # (1) IdP asserts the victim's email with NO email_verified → takeover refused, no session.
    monkeypatch.setattr(
        sso, "oauth_client",
        lambda row: _StubClient(victim, id_token_claims={"email": victim, "name": "x"}),
    )
    async with client_for(tenant.host) as c:
        await _configure(c, headers)
        resp = await c.get("/api/v1/auth/oidc/callback")
        assert resp.status_code in (302, 307)
        assert "error=oidc" in resp.headers["location"]
        assert "schakl_auth=" not in resp.headers.get("set-cookie", "")

    # (2) Same email, but email_verified true → legitimate linking still works.
    monkeypatch.setattr(
        sso, "oauth_client",
        lambda row: _StubClient(
            victim, id_token_claims={"email": victim, "email_verified": True, "name": "x"}
        ),
    )
    async with client_for(tenant.host) as c:
        resp = await c.get("/api/v1/auth/oidc/callback")
        assert resp.status_code in (302, 307)
        assert "schakl_auth=" in resp.headers.get("set-cookie", "")


async def test_FIXED_dangerous_url_schemes_rejected(client_for) -> None:
    """web-XSS: javascript:/data: URLs in company website and task links are refused at the API,
    so they can never reach an href. Legitimate http(s)/bare URLs still work."""
    a = await make_tenant("adv-xss")
    h = await auth_cookie(a.user)
    async with client_for(a.host) as c:
        assert (
            await c.post(
                "/api/v1/companies",
                json={"name": "x", "website": "javascript:alert(document.domain)"},
                headers=h,
            )
        ).status_code == 422
        ok = await c.post(
            "/api/v1/companies", json={"name": "y", "website": "https://example.com"}, headers=h
        )
        assert ok.status_code == 201

        task = await c.post("/api/v1/tasks", json={"title": "t"}, headers=h)
        assert task.status_code == 201, task.text
        tid = task.json()["id"]
        # javascript:// survives the "://" heuristic — must be rejected.
        assert (
            await c.post(
                f"/api/v1/tasks/{tid}/links",
                json={"url": "javascript://%0aalert(1)"}, headers=h,
            )
        ).status_code == 422
        good = await c.post(
            f"/api/v1/tasks/{tid}/links", json={"url": "example.com/page"}, headers=h
        )
        assert good.status_code in (200, 201), good.text


def test_FIXED_production_refuses_default_secret() -> None:
    """F1: the API refuses to boot in production on a default/known/short signing secret, while
    development is unaffected. This is the one fix the HTTP tests can't reach — they run in
    development, where the guard is inert by design."""
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    for bad in (
        "change-me-in-production-please-32bytes-min",  # config.py field default
        "dev-secret-change-me-in-production",          # shipped compose/.env sample
        "tooshort",
        "",
    ):
        with pytest.raises(ValidationError):
            Settings(environment="production", secret_key=bad)

    # A strong secret boots in production; a weak one is fine in development.
    assert Settings(environment="production", secret_key="x" * 40).is_production is True
    assert (
        Settings(
            environment="development", secret_key="change-me-in-production-please-32bytes-min"
        ).is_production
        is False
    )
