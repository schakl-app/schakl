"""Phase-2 adversarial tests — verify the second batch of audit fixes (F7, F10, F15, F19).

Same shape as ``test_audit_adversarial.py``: real HTTP through the full tenancy/RLS/RBAC stack.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member


async def _company(client, headers, name: str = "Acme") -> str:
    r = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _membership_id_of(client, owner_headers, email: str) -> str:
    members = (await client.get("/api/v1/members", headers=owner_headers)).json()
    return next(m["membership_id"] for m in members if m["email"] == email)


# --------------------------------------------------------------------------- #
# F7 — activity-feed BOLA: the trail needs read access to the entity, not just activity.read
# --------------------------------------------------------------------------- #
async def test_FIXED_activity_trail_requires_entity_read(client_for) -> None:
    tenant = await make_tenant("p2-bola")
    owner_h = await auth_cookie(tenant.user)
    member = await add_member(tenant, role="member")
    member_h = await auth_cookie(member)

    async with client_for(tenant.host) as c:
        cid = await _company(c, owner_h, "Trail Co")
        # A custom role holding ONLY activity.read — deliberately no companies.company.read.
        role = await c.post(
            "/api/v1/roles",
            json={"key": "trailonly", "name_i18n": {"en": "t"}, "permissions": ["activity.read"]},
            headers=owner_h,
        )
        assert role.status_code == 201, role.text
        rid = role.json()["id"]
        mid = await _membership_id_of(c, owner_h, member.email)
        assert (
            await c.put(f"/api/v1/members/{mid}/roles", json={"role_ids": [rid]}, headers=owner_h)
        ).status_code == 200

        # Holds activity.read but cannot read companies → the company's trail is 403, not leaked.
        blocked = await c.get(
            f"/api/v1/activity?entity_type=company&entity_id={cid}", headers=member_h
        )
        assert blocked.status_code == 403
        # The owner (wildcard) still reads it.
        assert (
            await c.get(f"/api/v1/activity?entity_type=company&entity_id={cid}", headers=owner_h)
        ).status_code == 200


# --------------------------------------------------------------------------- #
# F19 — a cross-tenant parent FK is refused (was a dangling ref + a 500 existence oracle)
# --------------------------------------------------------------------------- #
async def test_FIXED_cross_tenant_parent_fk_is_404(client_for) -> None:
    a = await make_tenant("p2-fk-a")
    b = await make_tenant("p2-fk-b")
    a_h = await auth_cookie(a.user)
    b_h = await auth_cookie(b.user)

    async with client_for(b.host) as cb:
        b_company = await _company(cb, b_h, "B Co")

    async with client_for(a.host) as ca:
        # A project in tenant A pointing at tenant B's company → 404 (parent not in tenant).
        proj = await ca.post(
            "/api/v1/projects", json={"name": "X", "company_id": b_company}, headers=a_h
        )
        assert proj.status_code == 404
        # A time entry pointing at B's company → 404.
        entry = await ca.post(
            "/api/v1/time/entries",
            json={"started_at": "2026-01-05T09:00:00Z", "minutes": 60, "company_id": b_company},
            headers=a_h,
        )
        assert entry.status_code == 404


# --------------------------------------------------------------------------- #
# F10 — CSV formula/DDE injection is neutralised (text only; numbers untouched)
# --------------------------------------------------------------------------- #
def test_FIXED_csv_formula_neutralized() -> None:
    from app.core.impex.service import _cell, _neutralize

    assert _neutralize("=SUM(A1)") == "'=SUM(A1)"
    assert _neutralize("+1") == "'+1"
    assert _neutralize("@cmd") == "'@cmd"
    assert _neutralize("normal text") == "normal text"
    # A negative *number* must not be mangled into text.
    assert _cell(-5) == "-5"
    assert _cell(3.5) == "3.5"
    # A string that looks like a formula is neutralised.
    assert _cell("=1+1") == "'=1+1"


# --------------------------------------------------------------------------- #
# F15 — production forces the Secure attribute on the auth cookie
# --------------------------------------------------------------------------- #
def test_FIXED_production_forces_secure_cookie() -> None:
    from app.config import Settings

    prod = Settings(environment="production", secret_key="x" * 40)
    assert prod.auth_cookie_secure is True, "production must emit a Secure auth cookie"
    # Development is unaffected — a plain-HTTP dev box keeps working.
    dev = Settings(environment="development", secret_key="x" * 40, auth_cookie_secure=False)
    assert dev.auth_cookie_secure is False
