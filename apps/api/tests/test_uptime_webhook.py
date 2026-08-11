"""Gate 3: the inbound webhook, its five gates, and the client-portal horizon."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.payments.tokens import mint
from app.modules.uptime import client as kuma_client
from app.modules.uptime.models import UptimeHeartbeat, UptimeInstance
from app.modules.uptime.webhook import MAX_BODY_BYTES
from tests.conftest import auth_cookie, make_tenant
from tests.uptime_fake import FakeKuma


@pytest.fixture
def kuma(monkeypatch) -> FakeKuma:
    fake = FakeKuma()
    monkeypatch.setattr(kuma_client, "_connector", fake.connector)
    return fake


async def _instance_with_monitor(c, headers, kuma: FakeKuma) -> tuple[str, dict, int]:
    inst = (
        await c.post(
            "/api/v1/uptime/instances",
            json={"name": "Kuma", "mode": "managed", "base_url": "https://kuma.example.nl"},
            headers=headers,
        )
    ).json()
    await c.post(
        f"/api/v1/uptime/instances/{inst['id']}/enrol",
        json={"username": "admin", "password": "secret"},
        headers=headers,
    )
    monitor = (
        await c.post(
            "/api/v1/uptime/monitors",
            json={"instance_id": inst["id"], "name": "site", "monitor_type": "http",
                  "target": "https://klant.nl"},
            headers=headers,
        )
    ).json()
    return inst["id"], monitor, monitor["kuma_monitor_id"]


async def _bind(session, org_id) -> None:
    """RLS is forced on every table here, so a bare session sees nothing."""
    from app.db import set_current_org

    await set_current_org(session, org_id)


async def _secret(session_maker, org_id, instance_id: str) -> str:
    """Read the instance's webhook secret directly.

    Binds the org first: RLS is forced on this table, so a bare session sees no rows at all —
    which is the guard working, not a fixture problem.
    """
    async with session_maker() as s:
        await _bind(s, org_id)
        row = await s.scalar(
            select(UptimeInstance).where(UptimeInstance.id == uuid.UUID(instance_id))
        )
        assert row is not None
        return row.webhook_secret


def _body(kuma_id: int, status: int = 0, when: str = "2026-01-02T03:04:05Z") -> dict:
    return {
        "monitor": {"id": kuma_id, "name": "site"},
        "heartbeat": {"status": status, "time": when, "ping": 120},
        "msg": "connect ECONNREFUSED",
    }


async def test_a_reported_heartbeat_lands_and_is_idempotent(client_for, kuma) -> None:
    """The same delivery twice inserts once. The guarantee is the index, not a check-then-insert:
    a flapping monitor and Kuma's own retries are in flight against each other."""
    from app.db import async_session_maker

    t = await make_tenant("uptime-hook")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id, monitor, kuma_id = await _instance_with_monitor(c, headers, kuma)
        secret = await _secret(async_session_maker, t.org.id, instance_id)
        token = mint(t.org.id, uuid.UUID(instance_id), secret)

        for _ in range(3):
            r = await c.post(f"/api/v1/uptime/hook/{token}", json=_body(kuma_id))
            assert r.status_code == 200, r.text

    async with async_session_maker() as s:
        await _bind(s, t.org.id)
        rows = (
            await s.execute(
                select(UptimeHeartbeat).where(
                    UptimeHeartbeat.monitor_id == uuid.UUID(monitor["id"])
                )
            )
        ).scalars().all()
    assert len(rows) == 1, "three identical deliveries wrote more than one row"
    assert rows[0].status == "down" and rows[0].reported is True


async def test_a_wrong_secret_and_an_unknown_instance_are_indistinguishable(
    client_for, kuma
) -> None:
    """Both a bare 404. A 401 would confirm the instance exists, and differing codes would make
    the route an oracle for what is configured here."""
    from app.db import async_session_maker

    t = await make_tenant("uptime-hook-secret")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id, _monitor, kuma_id = await _instance_with_monitor(c, headers, kuma)
        await _secret(async_session_maker, t.org.id, instance_id)

        wrong = mint(t.org.id, uuid.UUID(instance_id), "not-the-secret")
        unknown = mint(t.org.id, uuid.uuid4(), "whatever")
        malformed = "nonsense"
        for token in (wrong, unknown, malformed):
            r = await c.post(f"/api/v1/uptime/hook/{token}", json=_body(kuma_id))
            assert r.status_code == 404, token


async def test_an_unknown_monitor_is_refused_and_creates_nothing(client_for, kuma) -> None:
    """The token proves the *instance* is known, not that this monitor is. A route that
    auto-registers what it is told about is an unauthenticated writer of tenant rows."""
    from app.db import async_session_maker

    t = await make_tenant("uptime-hook-unknown")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id, _m, _k = await _instance_with_monitor(c, headers, kuma)
        secret = await _secret(async_session_maker, t.org.id, instance_id)
        token = mint(t.org.id, uuid.UUID(instance_id), secret)

        r = await c.post(f"/api/v1/uptime/hook/{token}", json=_body(9999))
        assert r.status_code == 404

    async with async_session_maker() as s:
        await _bind(s, t.org.id)
        assert (await s.execute(select(UptimeHeartbeat))).scalars().all() == []


async def test_an_oversized_body_is_refused_before_it_is_parsed(client_for, kuma) -> None:
    from app.db import async_session_maker

    t = await make_tenant("uptime-hook-big")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id, _m, kuma_id = await _instance_with_monitor(c, headers, kuma)
        secret = await _secret(async_session_maker, t.org.id, instance_id)
        token = mint(t.org.id, uuid.UUID(instance_id), secret)

        r = await c.post(
            f"/api/v1/uptime/hook/{token}",
            content=b"x" * (MAX_BODY_BYTES + 1),
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 413


async def test_deactivating_an_instance_withdraws_a_url_already_handed_out(
    client_for, kuma
) -> None:
    """An off switch for a credential you cannot collect back has to be retroactive (#304)."""
    from app.db import async_session_maker

    t = await make_tenant("uptime-hook-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id, _m, kuma_id = await _instance_with_monitor(c, headers, kuma)
        secret = await _secret(async_session_maker, t.org.id, instance_id)
        token = mint(t.org.id, uuid.UUID(instance_id), secret)
        live = await c.post(f"/api/v1/uptime/hook/{token}", json=_body(kuma_id))
        assert live.status_code == 200

        await c.patch(
            f"/api/v1/uptime/instances/{instance_id}", json={"active": False}, headers=headers
        )
        r = await c.post(f"/api/v1/uptime/hook/{token}", json=_body(kuma_id, status=1))
        assert r.status_code == 404


async def test_a_token_never_reaches_another_tenants_instance(client_for, kuma) -> None:
    """The org travels in the token, so nothing is read unscoped — and an org that does not own
    the instance cannot address it even with the right instance id."""
    from app.db import async_session_maker

    a = await make_tenant("uptime-hook-a")
    b = await make_tenant("uptime-hook-b")
    a_headers = await auth_cookie(a.user)
    async with client_for(a.host) as c:
        instance_id, _m, kuma_id = await _instance_with_monitor(c, a_headers, kuma)
        secret = await _secret(async_session_maker, a.org.id, instance_id)

    crossed = mint(b.org.id, uuid.UUID(instance_id), secret)
    async with client_for(b.host) as c:
        r = await c.post(f"/api/v1/uptime/hook/{crossed}", json=_body(kuma_id))
        assert r.status_code == 404


async def test_a_future_timestamp_is_clamped_to_now(client_for, kuma) -> None:
    """A body-supplied clock that could claim tomorrow would let one leaked URL pin a monitor's
    latest state permanently."""
    from app.db import async_session_maker

    t = await make_tenant("uptime-hook-clock")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id, monitor, kuma_id = await _instance_with_monitor(c, headers, kuma)
        secret = await _secret(async_session_maker, t.org.id, instance_id)
        token = mint(t.org.id, uuid.UUID(instance_id), secret)
        await c.post(
            f"/api/v1/uptime/hook/{token}", json=_body(kuma_id, when="2099-01-01T00:00:00Z")
        )

    from datetime import UTC, datetime

    async with async_session_maker() as s:
        await _bind(s, t.org.id)
        row = await s.scalar(
            select(UptimeHeartbeat).where(
                UptimeHeartbeat.monitor_id == uuid.UUID(monitor["id"])
            )
        )
    assert row is not None and row.observed_at <= datetime.now(UTC)
    # And a *past* timestamp is kept as-is, so a delayed delivery lands in the right minute.
    assert row.observed_at.year == datetime.now(UTC).year


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, "down"), (1, "up"), (2, "pending"), (3, "maintenance"), (99, "pending")],
)
def test_every_kuma_status_maps_to_something(raw: int, expected: str) -> None:
    """A new state in a future version must not make a client's outage invisible."""
    from app.modules.uptime.webhook import _status_of

    assert _status_of({"heartbeat": {"status": raw}}) == expected


def test_the_textual_shape_is_read_too() -> None:
    """A tenant may template their own webhook body; guessing wrong records an outage as an
    all-clear."""
    from app.modules.uptime.webhook import _status_of

    assert _status_of({"status": "down"}) == "down"
    assert _status_of({"status": "up"}) == "up"


def test_the_portal_horizon_is_stricter_than_the_staff_one() -> None:
    """Staff see a monitor attached to no client; a client must not — that row is the agency's
    own infrastructure (#266's rule, and why the clause lives on the model)."""
    from app.modules.uptime.models import UptimeMonitor

    assert hasattr(UptimeMonitor, "__portal_horizon_clause__")
    empty = UptimeMonitor.__portal_horizon_clause__(None)
    assert empty is not None, "a client with no company scope must see nothing, not everything"
