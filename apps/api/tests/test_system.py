"""Instance health, version and diagnostics (issue #18).

The system surfaces describe the **installation**, not a tenant, so the isolation question is
inverted from every other module: instead of "can org A see org B's rows", it is "can someone
who should not see the instance's internals reach them at all". Hence the role matrix below.

Redis is not guaranteed to exist wherever these run, so every test that asserts on a probe's
outcome stubs the probe. Only the shape tests touch the real thing.
"""

from __future__ import annotations

import pytest

import app.core.cache as cache
import app.core.system as system
from app.config import settings
from tests.conftest import auth_cookie, make_tenant


@pytest.fixture(autouse=True)
async def _reset_redis():
    """A redis-py asyncio client binds its connections to the loop that created them.

    pytest-asyncio gives each test a fresh loop, so a module-level client leaked from a prior
    test raises "attached to a different loop". Drop it around every test.
    """
    cache._client = None
    yield
    client = cache._client
    cache._client = None
    if client is not None:
        await client.aclose()


def _stub_probes(monkeypatch, *, redis_ok=True, worker_ok=True):
    """Pin the dependency probes so assertions don't depend on a live Redis."""

    async def fake_redis():
        return {"status": "ok" if redis_ok else "down", "version": "7.2.4" if redis_ok else None}

    async def fake_worker():
        return {
            "status": "ok" if worker_ok else "down",
            "last_seen_at": "2026-07-09T12:00:00+00:00" if worker_ok else None,
            "queue_depth": 0,
        }

    monkeypatch.setattr(system, "probe_redis", fake_redis)
    monkeypatch.setattr(system, "probe_worker", fake_worker)


# --------------------------------------------------------------------------- #
# /health — liveness
# --------------------------------------------------------------------------- #
async def test_health_is_unauthenticated_and_dependency_free(client_for):
    """It must stay cheap: a probe that touched Postgres would restart a healthy API."""
    async with client_for("schakl.localhost") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# /health/ready — readiness
# --------------------------------------------------------------------------- #
async def test_ready_is_ok_when_every_probe_passes(client_for, monkeypatch):
    _stub_probes(monkeypatch)
    async with client_for("schakl.localhost") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_is_degraded_and_detail_free_when_a_dependency_is_down(
    client_for, monkeypatch
):
    """503, and the body never names the failing dependency — it is unauthenticated."""
    _stub_probes(monkeypatch, redis_ok=False)
    async with client_for("schakl.localhost") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body == {"status": "degraded"}
    assert "redis" not in response.text.lower()


async def test_ready_is_degraded_when_migrations_are_behind(client_for, monkeypatch):
    _stub_probes(monkeypatch)

    async def behind(_session):
        return {"current": ["deadbeef"], "head": ["c0ffee"], "up_to_date": False}

    monkeypatch.setattr(system, "migration_status", behind)
    async with client_for("schakl.localhost") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}


# --------------------------------------------------------------------------- #
# /api/v1/system/info — role matrix
# --------------------------------------------------------------------------- #
async def test_system_info_rejects_anonymous(client_for):
    tenant = await make_tenant("sysanon")
    async with client_for(tenant.host) as client:
        response = await client.get("/api/v1/system/info")
    assert response.status_code == 401


@pytest.mark.parametrize("role", ["member", "client"])
async def test_system_info_is_manager_gated(client_for, monkeypatch, role):
    """Version and dependency detail are reconnaissance; a member has no business reading it."""
    _stub_probes(monkeypatch)
    tenant = await make_tenant(f"sys{role}", role=role)
    async with client_for(tenant.host) as client:
        response = await client.get(
            "/api/v1/system/info", headers=await auth_cookie(tenant.user)
        )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "errors.forbidden"


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_system_info_is_readable_by_managers(client_for, monkeypatch, role):
    _stub_probes(monkeypatch)
    tenant = await make_tenant(f"sysmgr{role}", role=role)
    async with client_for(tenant.host) as client:
        response = await client.get(
            "/api/v1/system/info", headers=await auth_cookie(tenant.user)
        )

    assert response.status_code == 200
    body = response.json()
    assert body["build"]["version"] == settings.version
    assert body["build"]["environment"] == settings.environment
    assert body["build"]["python_version"]
    # The suite runs against a database migrated to head.
    assert body["migrations"]["up_to_date"] is True
    assert body["migrations"]["current"] == body["migrations"]["head"]
    assert body["database"]["status"] == "ok"
    assert body["redis"] == {"status": "ok", "version": "7.2.4"}
    assert body["worker"]["status"] == "ok"
    assert "companies" in body["enabled_modules"]


async def test_system_info_describes_the_instance_not_the_tenant(client_for, monkeypatch):
    """Two orgs on one box get the same answer, and no org identifier ever appears in it."""
    _stub_probes(monkeypatch)
    a = await make_tenant("sysinst-a")
    b = await make_tenant("sysinst-b")

    async with client_for(a.host) as client:
        first = await client.get("/api/v1/system/info", headers=await auth_cookie(a.user))
    async with client_for(b.host) as client:
        second = await client.get("/api/v1/system/info", headers=await auth_cookie(b.user))

    assert first.status_code == second.status_code == 200
    assert first.json()["build"] == second.json()["build"]
    for org_id in (str(a.org.id), str(b.org.id)):
        assert org_id not in first.text
        assert org_id not in second.text
    assert "org_id" not in first.text


async def test_probe_redis_reads_a_live_server():
    """Exercises the real client. Every other test stubs it, so without this the actual
    ``Redis.from_url`` → ``INFO server`` path would never run anywhere."""
    result = await system.probe_redis()
    if result["status"] == "down":
        pytest.skip("no Redis reachable at SCHAKL_REDIS_URL")
    assert result["version"]


async def test_system_info_survives_a_dead_worker_and_redis(client_for, monkeypatch):
    """A degraded box must still render its own diagnostics — that is when they are needed."""
    _stub_probes(monkeypatch, redis_ok=False, worker_ok=False)
    tenant = await make_tenant("sysdegraded")
    async with client_for(tenant.host) as client:
        response = await client.get(
            "/api/v1/system/info", headers=await auth_cookie(tenant.user)
        )

    assert response.status_code == 200
    body = response.json()
    assert body["redis"]["status"] == "down"
    assert body["worker"]["status"] == "down"
    assert body["database"]["status"] == "ok"
