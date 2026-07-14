"""Public demo mode (issue #141): the forced-safe posture and the enumerable demo-guard.

The guard test mirrors ``test_rbac_deny_by_default``: with demo mode on, every catalogued
operation must return ``errors.demo_blocked``; with it off, the guard is inert. The catalog is the
single source of truth, so this doubles as the review checklist when a module ships a dangerous
endpoint.
"""

from __future__ import annotations

from app.config import Settings
from app.config import settings as app_settings
from app.core.demo.guard import DEMO_BLOCKED_RULES
from tests.conftest import auth_cookie, make_tenant


def test_demo_mode_forces_safe_posture() -> None:
    """SCHAKL_DEMO_MODE forces registration + the instance-admin surface (hence impersonation) off,
    no matter what the rest of the env says — the whole point of a *public* posture."""
    forced = Settings(demo_mode=True, allow_registration=True, instance_admin_enabled=True)
    assert forced.allow_registration is False
    assert forced.instance_admin_enabled is False

    normal = Settings(demo_mode=False, allow_registration=True, instance_admin_enabled=True)
    assert normal.allow_registration is True
    assert normal.instance_admin_enabled is True


async def test_demo_guard_blocks_every_catalogued_op(client_for, monkeypatch) -> None:
    """With demo mode on, each rule in the catalog returns the errors.demo_blocked envelope —
    before routing/auth, so it blocks whether or not the caller is signed in."""
    t = await make_tenant("demo-guard")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(app_settings, "demo_mode", True)
    async with client_for(t.host) as c:
        for methods, prefix in DEMO_BLOCKED_RULES:
            method = "POST" if "POST" in methods else sorted(methods)[0]
            resp = await c.request(method, f"/api/v1{prefix}", headers=headers)
            assert resp.status_code == 403, (method, prefix, resp.status_code)
            assert resp.json()["error"]["code"] == "demo_blocked", (method, prefix)


async def test_setup_blocked_in_demo(client_for, monkeypatch) -> None:
    """/setup is not in the middleware catalog (it must answer /status), but run_setup itself
    refuses in demo mode — the seeder owns org creation."""
    t = await make_tenant("demo-setup")
    monkeypatch.setattr(app_settings, "demo_mode", True)
    async with client_for(t.host) as c:
        resp = await c.post(
            "/api/v1/setup",
            json={
                "slug": "visitor",
                "org_name": "Visitor",
                "email": "visitor@example.com",
                "password": "secret1234",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "demo_blocked"
        # setup/status must not advertise the wizard either.
        status = await c.get("/api/v1/setup/status")
        assert status.json()["needs_setup"] is False


async def test_demo_guard_inert_when_off(client_for) -> None:
    """Off (the default), a catalogued path is handled normally — never demo_blocked. Here an
    unauthenticated write hits auth (401), proving the guard did not intercept."""
    t = await make_tenant("demo-off")
    async with client_for(t.host) as c:
        resp = await c.post("/api/v1/api-keys", json={"name": "k"})
        assert resp.json().get("error", {}).get("code") != "demo_blocked"
