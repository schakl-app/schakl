"""A site that is down does not read like a site that is up (#356).

`/websites` drew a green pill from ``uptime_enabled`` — a tick in a box — and green is this
product's healthy state, so two hours of downtime looked exactly like two hours of uptime. The
state existed one module over and had no way across; :mod:`app.core.monitoring` is that way.

What these pin is the vocabulary, because the failure is a *rendering* one and every value here
comes back as a 200 either way: `up`, `down`, and the third state — monitored but never observed
— which must not collapse into either colour.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db import async_session_maker, set_current_org
from app.modules.uptime import client as kuma_client
from app.modules.uptime.models import UptimeHeartbeat
from tests.conftest import auth_cookie, make_tenant
from tests.uptime_fake import FakeKuma


@pytest.fixture
def kuma(monkeypatch) -> FakeKuma:
    fake = FakeKuma()
    monkeypatch.setattr(kuma_client, "_connector", fake.connector)
    return fake


async def _website(c, headers) -> str:
    company = (
        await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
    ).json()["id"]
    domain = (
        await c.post(
            "/api/v1/domains", json={"name": "klant.nl", "company_id": company}, headers=headers
        )
    ).json()["id"]
    created = await c.post(
        "/api/v1/websites",
        json={"domain_id": domain, "uptime_enabled": True},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _monitor(c, headers, kuma: FakeKuma, website_id: str) -> dict:
    instance = (
        await c.post(
            "/api/v1/uptime/instances",
            json={"name": "Kuma", "mode": "managed", "base_url": "https://kuma.example.nl"},
            headers=headers,
        )
    ).json()
    await c.post(
        f"/api/v1/uptime/instances/{instance['id']}/enrol",
        json={"username": "admin", "password": "secret"},
        headers=headers,
    )
    monitor = (
        await c.post(
            "/api/v1/uptime/monitors",
            json={
                "instance_id": instance["id"],
                "name": "site",
                "monitor_type": "http",
                "target": "https://klant.nl",
                "website_id": website_id,
            },
            headers=headers,
        )
    ).json()
    return monitor


async def _heartbeat(org_id, monitor_id: str, status: str, minutes_ago: int) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        session.add(
            UptimeHeartbeat(
                id=uuid.uuid4(),
                org_id=org_id,
                monitor_id=uuid.UUID(monitor_id),
                status=status,
                observed_at=datetime.now(UTC).replace(microsecond=0)
                - timedelta(minutes=minutes_ago),
                reported=True,
            )
        )
        await session.commit()


async def _listed(c, headers) -> dict:
    page = (await c.get("/api/v1/websites", headers=headers)).json()
    assert page["total"] == 1, page
    return page["items"][0]


async def test_the_flag_alone_never_claims_the_site_is_up(client_for) -> None:
    """`uptime_enabled` is configuration. On its own it answers `None`, not "up"."""
    t = await make_tenant("web-uptime-flag")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await _website(c, headers)
        row = await _listed(c, headers)
        assert row["uptime_enabled"] is True
        assert row["uptime_status"] is None


async def test_a_monitor_with_no_heartbeat_is_not_down(client_for, kuma) -> None:
    """*Watched, never observed* is its own answer — collapsing it into "down" invents an
    outage nobody measured."""
    t = await make_tenant("web-uptime-unobserved")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        website = await _website(c, headers)
        await _monitor(c, headers, kuma, website)
        assert (await _listed(c, headers))["uptime_status"] is None


async def test_the_last_heartbeat_is_the_answer(client_for, kuma) -> None:
    """A monitor that flapped up and back down reports down: the *last* one, not the newest row
    of anything else."""
    t = await make_tenant("web-uptime-last")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        website = await _website(c, headers)
        monitor = await _monitor(c, headers, kuma, website)

        await _heartbeat(t.org.id, monitor["id"], "down", minutes_ago=30)
        await _heartbeat(t.org.id, monitor["id"], "up", minutes_ago=20)
        assert (await _listed(c, headers))["uptime_status"] == "up"

        await _heartbeat(t.org.id, monitor["id"], "down", minutes_ago=1)
        assert (await _listed(c, headers))["uptime_status"] == "down"


async def test_one_failing_check_is_not_hidden_behind_a_passing_one(client_for, kuma) -> None:
    """Two monitors on one site (apex and a keyword check): the site is down if either is."""
    t = await make_tenant("web-uptime-worst")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        website = await _website(c, headers)
        first = await _monitor(c, headers, kuma, website)
        second = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={
                    "instance_id": first["instance_id"],
                    "name": "keyword",
                    "monitor_type": "http",
                    "target": "https://klant.nl/status",
                    "website_id": website,
                },
                headers=headers,
            )
        ).json()

        await _heartbeat(t.org.id, first["id"], "up", minutes_ago=2)
        await _heartbeat(t.org.id, second["id"], "down", minutes_ago=2)
        assert (await _listed(c, headers))["uptime_status"] == "down"


async def test_the_status_never_crosses_a_tenant(client_for, kuma) -> None:
    a = await make_tenant("web-uptime-iso-a")
    b = await make_tenant("web-uptime-iso-b")
    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        website = await _website(c, headers)
        monitor = await _monitor(c, headers, kuma, website)
        await _heartbeat(a.org.id, monitor["id"], "down", minutes_ago=1)
    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        await _website(c, headers)
        assert (await _listed(c, headers))["uptime_status"] is None
