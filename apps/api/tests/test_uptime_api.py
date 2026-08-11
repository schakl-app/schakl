"""The uptime module's API surface: enrolment, probing, the read-only sync, isolation.

Every test drives the fake Uptime Kuma through the client's single connector seam — nothing here
touches the network.
"""

from __future__ import annotations

import pytest

from app.modules.uptime import client as kuma_client
from tests.conftest import auth_cookie, make_tenant
from tests.uptime_fake import FakeKuma


@pytest.fixture
def kuma(monkeypatch) -> FakeKuma:
    fake = FakeKuma()
    monkeypatch.setattr(kuma_client, "_connector", fake.connector)
    return fake


async def _instance(c, headers, **overrides) -> dict:
    body = {"name": "Kuma", "mode": "managed", "base_url": "https://kuma.example.nl"} | overrides
    response = await c.post("/api/v1/uptime/instances", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------- instances


async def test_instance_crud_and_the_credential_is_never_returned(client_for, kuma) -> None:
    t = await make_tenant("uptime-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await _instance(c, headers)
        assert created["status"] == "pending"
        assert created["token_configured"] is False
        assert "token" not in created and "password" not in created

        await c.post(
            f"/api/v1/uptime/instances/{created['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        got = (await c.get(f"/api/v1/uptime/instances/{created['id']}", headers=headers)).json()
        assert got["token_configured"] is True, "an enrolled instance must read as configured"
        assert "secret" not in str(got) and "fake-jwt-token" not in str(got)

        assert (
            await c.delete(f"/api/v1/uptime/instances/{created['id']}", headers=headers)
        ).status_code == 204


async def test_a_managed_instance_needs_an_address(client_for, kuma) -> None:
    t = await make_tenant("uptime-url")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/uptime/instances",
            json={"name": "No url", "mode": "managed"},
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["message"] == "errors.uptime_base_url_required"


async def test_a_linked_instance_needs_no_credential_and_is_never_dialled(client_for, kuma) -> None:
    """`linked` is the mode for a client-hosted Kuma nobody will hand over. It must be creatable
    with nothing, and must refuse to connect rather than half-trying."""
    t = await make_tenant("uptime-linked")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await _instance(c, headers, mode="linked", base_url=None, name="Client Kuma")
        assert created["mode"] == "linked"
        r = await c.post(f"/api/v1/uptime/instances/{created['id']}/sync", headers=headers)
        assert r.status_code == 200
        assert r.json()["error"] == "errors.uptime_not_enrolled"
        assert kuma.connections == [], "a linked instance was dialled"


# ------------------------------------------------------------------- enrolment


async def test_enrolment_stores_a_token_and_never_the_password(client_for, kuma) -> None:
    t = await make_tenant("uptime-enrol")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        r = await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        assert r.status_code == 200 and r.json()["ok"] is True
        assert r.json()["server_version"] == "2.5.0"

        # A probe afterwards must work with no password anywhere in the system.
        probe = await c.post(f"/api/v1/uptime/instances/{inst['id']}/probe", headers=headers)
        assert probe.json()["ok"] is True and probe.json()["status"] == "active"


async def test_a_refusal_is_reported_not_raised_and_is_recorded(client_for, kuma) -> None:
    """The report *is* the answer. Raising would roll back the very status update that makes
    the failure visible on the settings screen."""
    t = await make_tenant("uptime-refused")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        r = await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "wrong"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert r.json()["error"] == "errors.uptime_credentials_rejected"

        stored = (await c.get(f"/api/v1/uptime/instances/{inst['id']}", headers=headers)).json()
        assert stored["status"] == "error"
        assert stored["last_error"], "the failure was not persisted"


async def test_a_revoked_token_reads_as_needs_reauth_not_error(client_for, kuma) -> None:
    """Same shape, opposite instruction — and an admin told "wrong credential" would rotate
    something that was never wrong."""
    t = await make_tenant("uptime-reauth")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        kuma.token_revoked = True
        r = await c.post(f"/api/v1/uptime/instances/{inst['id']}/probe", headers=headers)
        assert r.json()["error"] == "errors.uptime_reauth_required"
        assert r.json()["status"] == "needs_reauth"


async def test_a_successful_call_clears_a_previous_error(client_for, kuma) -> None:
    """A health flag that only ever turns on is a bug with a long tail: a row nothing is wrong
    with keeps its red line through every sync that works."""
    t = await make_tenant("uptime-recover")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        kuma.unreachable = True
        await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        broken = (await c.get(f"/api/v1/uptime/instances/{inst['id']}", headers=headers)).json()
        assert broken["status"] == "error" and broken["last_error"]

        kuma.unreachable = False
        r = await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        assert r.json()["ok"] is True
        healed = (await c.get(f"/api/v1/uptime/instances/{inst['id']}", headers=headers)).json()
        assert healed["status"] == "active"
        assert healed["last_error"] is None, "the error flag never cleared"


# ------------------------------------------------------------------------ sync


async def test_sync_mirrors_monitors_and_never_writes_back(client_for, kuma) -> None:
    kuma.add(name="site", url="https://klant.nl")
    kuma.add(name="api", url="https://api.klant.nl", interval=120)
    before = dict(kuma.monitors)

    t = await make_tenant("uptime-sync")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        report = (
            await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=headers)
        ).json()
        assert report["ok"] is True and report["seen"] == 2 and report["created"] == 2

        listed = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()
        assert listed["total"] == 2
        names = sorted(m["name"] for m in listed["items"])
        assert names == ["api", "site"]
        assert all(m["sync_status"] == "active" for m in listed["items"])

    assert kuma.monitors == before, "the read-only sync modified the far end"


async def test_sync_is_idempotent_and_marks_what_vanished(client_for, kuma) -> None:
    kuma.add(name="one")
    kuma.add(name="two")
    t = await make_tenant("uptime-resync")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=headers)

        second = (
            await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=headers)
        ).json()
        assert second["created"] == 0 and second["updated"] == 2

        kuma.monitors.pop(2)
        third = (
            await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=headers)
        ).json()
        assert third["missing"] == 1

        # Marked, never deleted: "it is gone from Kuma" and "we should forget it" are different
        # decisions, and only one of them is ours.
        listed = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()
        assert listed["total"] == 2
        gone = [m for m in listed["items"] if m["sync_status"] == "missing"]
        assert len(gone) == 1 and gone[0]["name"] == "two"


async def test_the_mirror_never_holds_a_credential(client_for, kuma) -> None:
    kuma.add(name="secured", basic_auth_pass="CANARY-PASSWORD")
    t = await make_tenant("uptime-secret")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=headers)
        listed = await c.get("/api/v1/uptime/monitors", headers=headers)
        assert "CANARY-PASSWORD" not in listed.text
        # The fingerprint is an oracle; it is a comparison value, not a response field.
        assert "fp" not in listed.text


async def test_group_parenthood_survives_a_child_arriving_first(client_for, kuma) -> None:
    """Kuma's ids are not ordered by hierarchy, so resolving inline drops forward edges."""
    child = kuma.add(name="child", parent=99)
    group = kuma.add(name="group", type="group")
    kuma.monitors[child]["parent"] = group

    t = await make_tenant("uptime-group")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=headers)
        items = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()["items"]
        by_name = {m["name"]: m for m in items}
        assert by_name["group"]["monitor_type"] == "group"
        assert by_name["child"]["parent_id"] == by_name["group"]["id"]
        assert by_name["group"]["parent_id"] is None


async def test_a_group_is_nameable_counted_and_watches_nothing(client_for, kuma) -> None:
    """What "groups synced into the CRM" has to mean on a screen, in three parts.

    ``parent_id`` alone cannot be rendered — it is a uuid — so the group's *name* rides the read
    under ``meta=true`` and is skipped without it, because a picker throws it away. The instance
    says how many of its monitors are folders, which is how an admin confirms the hierarchy came
    across at all. And a group carries **no target**: Kuma stores it a ``url`` of ``"https://"``,
    its own form's placeholder, which would otherwise render as a monitor pointed at a broken
    address rather than as the folder it is.
    """
    group = kuma.add_group("breik. hosting klanten")
    kuma.add(name="kuzee", parent=group, url="https://kuzee.com")

    t = await make_tenant("uptime-group-meta")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        report = (
            await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=headers)
        ).json()
        assert report["seen"] == 2
        assert report["groups"] == 1, "the report cannot say the hierarchy arrived"

        items = (await c.get("/api/v1/uptime/monitors?meta=true", headers=headers)).json()["items"]
        by_name = {m["name"]: m for m in items}
        assert by_name["kuzee"]["parent_name"] == "breik. hosting klanten"
        assert by_name["breik. hosting klanten"]["target"] is None, "a group watches nothing"
        assert by_name["breik. hosting klanten"]["parent_name"] is None

        plain = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()["items"]
        assert all(m["parent_name"] is None for m in plain), "meta was resolved unasked"

        listed = (await c.get("/api/v1/uptime/instances", headers=headers)).json()
        assert listed[0]["monitor_count"] == 2
        assert listed[0]["group_count"] == 1


async def test_a_failed_sync_leaves_the_mirror_readable(client_for, kuma) -> None:
    """A probe is evidence, never the gate: an agency staring at an outage needs yesterday's
    mirror far more than it needs an empty screen."""
    kuma.add(name="site")
    t = await make_tenant("uptime-evidence")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inst = await _instance(c, headers)
        await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=headers)

        kuma.unreachable = True
        failed = (
            await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=headers)
        ).json()
        assert failed["ok"] is False

        listed = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()
        assert listed["total"] == 1, "a failed sync emptied the mirror"
        assert listed["items"][0]["last_observed_at"] is not None


# ------------------------------------------------------------------- isolation


async def test_monitors_never_cross_tenants(client_for, kuma) -> None:
    kuma.add(name="tenant-a-monitor")
    a = await make_tenant("uptime-iso-a")
    b = await make_tenant("uptime-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as c:
        inst = await _instance(c, a_headers)
        await c.post(
            f"/api/v1/uptime/instances/{inst['id']}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=a_headers,
        )
        await c.post(f"/api/v1/uptime/instances/{inst['id']}/sync", headers=a_headers)
        monitor_id = (await c.get("/api/v1/uptime/monitors", headers=a_headers)).json()["items"][0][
            "id"
        ]

    async with client_for(b.host) as c:
        assert (await c.get("/api/v1/uptime/instances", headers=b_headers)).json() == []
        assert (await c.get("/api/v1/uptime/monitors", headers=b_headers)).json()["total"] == 0
        # By id, and it must be a 404 rather than a 403 — a 403 leaks existence.
        assert (
            await c.get(f"/api/v1/uptime/monitors/{monitor_id}", headers=b_headers)
        ).status_code == 404
        assert (
            await c.get(f"/api/v1/uptime/instances/{inst['id']}", headers=b_headers)
        ).status_code == 404
