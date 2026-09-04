"""Browser push notifications (#309): the crypto, the channel, the sweep, and the isolation.

No network anywhere: :func:`app.core.webpush.send` is monkeypatched and the calls are captured.
The one thing that *is* exercised for real is the encryption, against the RFC's own test vector —
a round-trip against our own decryption would pass on a wrong-but-symmetric mistake.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app.core import webpush as webpush_core
from app.core.models import Org
from app.db import async_session_maker, set_current_org
from app.modules.notifications import webpush as webpush_module
from app.modules.notifications.defaults import ResolvedPref, default_event_pref
from app.modules.notifications.events import DIGEST_IMMEDIATE, EVENT_TYPES
from app.modules.notifications.models import NotificationDelivery, PushSubscription
from app.modules.notifications.prefs import compute_visible_at
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, default_company, leave_workday, make_tenant
from tests.test_notification_channels import _member

# A throwaway browser keypair, generated once: `p256dh` is a real P-256 point so the encryption
# path runs for real rather than against a placeholder that would never decrypt.
_UA_KEY = ec.generate_private_key(ec.SECP256R1())
UA_P256DH = webpush_core.b64url_encode(
    _UA_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
)
UA_AUTH = webpush_core.b64url_encode(b"0123456789abcdef")


def _subscription(endpoint: str = "https://fcm.googleapis.com/fcm/send/abc") -> dict:
    return {
        "endpoint": endpoint,
        "p256dh": UA_P256DH,
        "auth": UA_AUTH,
        "user_agent": "Chrome op Linux",
    }


# --------------------------------------------------------------------------- #
# The crypto, against the RFCs rather than against ourselves
# --------------------------------------------------------------------------- #
def test_encrypt_reproduces_the_rfc_8291_test_vector() -> None:
    """RFC 8291 §5, byte for byte.

    The only assertion that can tell "correct" from "self-consistent": every other check here
    encrypts and never decrypts, so a symmetric mistake in the key derivation would sail past.
    """

    def d(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    ua_public = (
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
    )
    as_private = ec.derive_private_key(
        int.from_bytes(d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1()
    )
    out = webpush_core.encrypt(
        b"When I grow up, I want to be a watermelon",
        p256dh=ua_public,
        auth="BTBZMqHH6r4Tts7J_aSIgg",
        salt=d("DGv6ra1nlYgDCS1FRnbzlw"),
        ephemeral=as_private,
    )
    assert webpush_core.b64url_encode(out) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
        "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
        "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_encrypt_is_never_deterministic() -> None:
    """A reused salt or ephemeral key leaks the plaintext, so production must randomise both."""
    args = {"p256dh": UA_P256DH, "auth": UA_AUTH}
    assert webpush_core.encrypt(b"x", **args) != webpush_core.encrypt(b"x", **args)


def test_vapid_header_is_an_es256_jwt_for_the_endpoint_origin() -> None:
    """The audience is the push service's origin, and the signature is raw r‖s, not DER.

    A DER signature is accepted by every naive local check and rejected by every push service,
    so the length is asserted rather than merely the shape of the string.
    """
    import json

    keys = webpush_core.generate_keys()
    headers = webpush_core.vapid_headers(
        "https://updates.push.services.mozilla.com/wpush/v1/abc",
        keys,
        subject="mailto:a@b.example",
    )
    token = headers["Authorization"].split("vapid t=")[1].split(",")[0]
    header_b64, claims_b64, signature_b64 = token.split(".")
    assert json.loads(webpush_core.b64url_decode(header_b64))["alg"] == "ES256"
    claims = json.loads(webpush_core.b64url_decode(claims_b64))
    assert claims["aud"] == "https://updates.push.services.mozilla.com"
    assert claims["sub"] == "mailto:a@b.example"
    assert claims["exp"] > int(datetime.now(UTC).timestamp())
    assert len(webpush_core.b64url_decode(signature_b64)) == 64
    assert f"k={keys.public_key}" in headers["Authorization"]


def test_endpoint_guard_refuses_private_and_non_https_targets() -> None:
    """The endpoint is the one attacker-supplied URL in this module (SSRF, CLAUDE.md §15)."""
    from app.core.net_guard import SsrfBlocked

    for bad in (
        "http://fcm.googleapis.com/fcm/send/x",  # plaintext
        "https://127.0.0.1/push",  # loopback
        "https://169.254.169.254/latest/meta-data",  # link-local metadata service
        "https://[::ffff:127.0.0.1]/push",  # the IPv4-mapped form net_guard unwraps
    ):
        with pytest.raises(SsrfBlocked):
            webpush_core.assert_endpoint_safe(bad)

    # A real push service passes — the guard must not be a blanket refusal.
    webpush_core.assert_endpoint_safe("https://fcm.googleapis.com/fcm/send/abc")


# --------------------------------------------------------------------------- #
# Quiet hours — collected since #16, read by nothing until now
# --------------------------------------------------------------------------- #
def _pref(**kwargs) -> ResolvedPref:  # noqa: ANN003
    base = {
        "enabled": True,
        "delay_minutes": 0,
        "digest": "immediate",
        "digest_time": None,
        "digest_weekday": None,
    }
    return ResolvedPref(**{**base, **kwargs})


def test_quiet_hours_move_a_night_time_push_to_the_morning() -> None:
    tz = ZoneInfo("Europe/Amsterdam")
    pref = _pref(quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0))
    at_three = datetime(2026, 3, 10, 2, 0, tzinfo=UTC)  # 03:00 local

    assert compute_visible_at(pref, at_three, tz=tz, quiet_hours=True).astimezone(tz).hour == 7
    # The bell is deliberately exempt: holding an in-app row back interrupts nobody and makes
    # the app look broken.
    assert compute_visible_at(pref, at_three, tz=tz) == at_three


def test_quiet_hours_evening_half_lands_on_tomorrow() -> None:
    """A wrapping window has two halves and they exit on different days."""
    tz = ZoneInfo("Europe/Amsterdam")
    pref = _pref(quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0))
    at_eleven = datetime(2026, 3, 10, 22, 0, tzinfo=UTC)  # 23:00 local

    out = compute_visible_at(pref, at_eleven, tz=tz, quiet_hours=True).astimezone(tz)
    assert (out.hour, out.day) == (7, 11)


def test_quiet_hours_handle_a_window_that_does_not_wrap() -> None:
    """12:00–13:00 is as valid as 22:00–07:00, and a wrap-only implementation never fires."""
    tz = ZoneInfo("Europe/Amsterdam")
    pref = _pref(quiet_hours_start=time(12, 0), quiet_hours_end=time(13, 0))
    lunch = datetime(2026, 3, 10, 11, 30, tzinfo=UTC)  # 12:30 local

    out = compute_visible_at(pref, lunch, tz=tz, quiet_hours=True).astimezone(tz)
    assert (out.hour, out.minute, out.day) == (13, 0, 10)


def test_quiet_hours_survive_a_dst_boundary() -> None:
    """Wall-clock arithmetic: the window ends at 07:00 local on the night the clocks move.

    The Netherlands springs forward at 02:00 on 29 March 2026, so a push arriving at 01:30 local
    must still surface at 07:00 local — an hour of UTC arithmetic would put it at 06:00.
    """
    tz = ZoneInfo("Europe/Amsterdam")
    pref = _pref(quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0))
    before_the_jump = datetime(2026, 3, 29, 0, 30, tzinfo=UTC)  # 01:30 local, CET

    out = compute_visible_at(pref, before_the_jump, tz=tz, quiet_hours=True).astimezone(tz)
    assert (out.hour, out.minute) == (7, 0)


def test_no_quiet_window_leaves_the_slot_alone() -> None:
    tz = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 3, 10, 2, 0, tzinfo=UTC)
    assert compute_visible_at(_pref(), now, tz=tz, quiet_hours=True) == now


async def test_quiet_hours_reach_every_pushed_channel_not_just_push(client_for) -> None:
    """The resolved pref for e-mail *and* for a personal chat channel carries the window.

    Without this, `quiet_hours=True` at those two call sites is a silent no-op — the flag is
    passed, the window resolves to `None`, and nothing is ever held back. That failure is
    invisible in every functional test, because holding nothing back looks exactly like the
    behaviour before quiet hours existed.
    """
    from app.modules.notifications.models import NotificationChannelConfig
    from app.modules.notifications.prefs import resolve_channel_prefs, resolve_email_for_recipients

    t = await make_tenant("quiet-everywhere")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        saved = await c.put(
            "/api/v1/notifications/preferences",
            json={"general": {"quiet_hours_start": "22:00", "quiet_hours_end": "07:00"}},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        made = await c.post(
            "/api/v1/notifications/channels",
            json={
                "kind": "slack",
                "name": "My DM",
                "url": "slack://T0/B0/XYZ",
                "user_id": str(t.user.id),
            },
            headers=headers,
        )
        assert made.status_code == 201, made.text

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        email = await resolve_email_for_recipients(
            session, t.org.id, "leave.requested", [t.user.id]
        )
        assert email[t.user.id].quiet_hours_start == time(22, 0)
        assert email[t.user.id].quiet_hours_end == time(7, 0)

        configs = (
            (await session.execute(select(NotificationChannelConfig))).scalars().all()
        )
        channel = await resolve_channel_prefs(
            session, t.org.id, "leave.requested", list(configs)
        )
        pref = channel[configs[0].id]
        assert pref.quiet_hours_start == time(22, 0)
        assert pref.quiet_hours_end == time(7, 0)


# --------------------------------------------------------------------------- #
# Registering a device
# --------------------------------------------------------------------------- #
async def test_config_mints_one_vapid_keypair_and_keeps_it(client_for) -> None:
    """The keypair is generated lazily and never rotated: rotating orphans every device."""
    t = await make_tenant("push-config")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        first = await c.get("/api/v1/notifications/push/config", headers=headers)
        assert first.status_code == 200, first.text
        key = first.json()["vapid_public_key"]
        assert len(webpush_core.b64url_decode(key)) == 65  # uncompressed P-256 point

        again = (await c.get("/api/v1/notifications/push/config", headers=headers)).json()
        assert again["vapid_public_key"] == key


async def test_register_is_idempotent_on_the_endpoint(client_for) -> None:
    """A browser re-presents its subscription every session; that is a refresh, not a new device."""
    t = await make_tenant("push-register")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        first = await c.post(
            "/api/v1/notifications/push/subscriptions", json=_subscription(), headers=headers
        )
        assert first.status_code == 201, first.text
        second = await c.post(
            "/api/v1/notifications/push/subscriptions", json=_subscription(), headers=headers
        )
        assert second.status_code == 201
        assert second.json()["id"] == first.json()["id"]

        listed = (
            await c.get("/api/v1/notifications/push/subscriptions", headers=headers)
        ).json()
        assert len(listed) == 1
        # The endpoint and the key material never come back: the row exists to be revoked.
        assert "endpoint" not in listed[0] and "p256dh" not in listed[0]
        assert listed[0]["user_agent"] == "Chrome op Linux"


async def test_register_refuses_an_endpoint_pointing_inside_the_network(client_for) -> None:
    """The request comes from a browser, but nothing about it proves that (SSRF)."""
    t = await make_tenant("push-ssrf")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/notifications/push/subscriptions",
            json=_subscription("https://169.254.169.254/latest/meta-data"),
            headers=headers,
        )
        assert res.status_code == 422, res.text
        assert res.json()["error"]["message"] == "errors.push_endpoint_blocked"


async def test_a_member_never_sees_or_revokes_another_persons_device(client_for) -> None:
    """Somebody else's id is a 404, never a 403 that would confirm the device exists (§15)."""
    t = await make_tenant("push-scope")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "emp@push-scope.example")
        mh = await auth_cookie(member)
        mine = await c.post(
            "/api/v1/notifications/push/subscriptions", json=_subscription(), headers=owner
        )
        assert mine.status_code == 201

        assert (await c.get("/api/v1/notifications/push/subscriptions", headers=mh)).json() == []
        stolen = await c.delete(
            f"/api/v1/notifications/push/subscriptions/{mine.json()['id']}", headers=mh
        )
        assert stolen.status_code == 404


async def test_push_subscriptions_are_tenant_isolated(client_for) -> None:
    """Golden Rule 1: one tenant's devices are invisible and unrevokable from another's host."""
    a = await make_tenant("push-tenant-a")
    b = await make_tenant("push-tenant-b")
    ah, bh = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as c:
        created = await c.post(
            "/api/v1/notifications/push/subscriptions", json=_subscription(), headers=ah
        )
        assert created.status_code == 201
        device_id = created.json()["id"]

    async with client_for(b.host) as c:
        # The same endpoint may exist in another tenant: it is a *different* row entirely.
        assert (await c.get("/api/v1/notifications/push/subscriptions", headers=bh)).json() == []
        assert (
            await c.delete(
                f"/api/v1/notifications/push/subscriptions/{device_id}", headers=bh
            )
        ).status_code == 404

    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        rows = (await session.execute(select(PushSubscription))).scalars().all()
        assert rows == []  # RLS, not just the query's WHERE clause


# --------------------------------------------------------------------------- #
# The fan-out
# --------------------------------------------------------------------------- #
async def _leave_request(c, owner, member_headers, offset: int = 0) -> None:  # noqa: ANN001
    """Emit one ``leave.requested`` the owner is notified about. Distinct days per ``offset``:
    two requests for the same day are an overlap the service rightly refuses."""
    types = (await c.get("/api/v1/leave/types", headers=owner)).json()
    special = next(x["id"] for x in types if x["key"] == "special")
    start = leave_workday(offset)
    res = await c.post(
        "/api/v1/leave/requests",
        json={
            "leave_type_id": special,
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
        },
        headers=member_headers,
    )
    assert res.status_code == 201, res.text


async def test_push_defaults_to_the_urgent_events_and_nothing_else(client_for) -> None:
    """Granting a browser permission is the opt-in, and it opts you into the *immediate* events.

    The split is the whole guarantee: an event whose own cadence is "tomorrow at 08:00" must not
    wake a phone, or the first thing anybody does is switch the channel off entirely. Asserted
    against the in-app cadence rather than a copied list, so adding an immediate event tomorrow
    cannot leave the two definitions of "urgent" disagreeing.
    """
    t = await make_tenant("push-default-on")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        matrix = (await c.get("/api/v1/notifications/preferences", headers=owner)).json()
        pushed = {row["event_type"] for row in matrix["events"] if row["push_enabled"]}
        expected = {
            event
            for event in EVENT_TYPES
            if default_event_pref(event).digest == DIGEST_IMMEDIATE
        }
        assert pushed == expected
        assert "task.commented" not in pushed  # a digest event stays silent

        await c.post(
            "/api/v1/notifications/push/subscriptions", json=_subscription(), headers=owner
        )
        member = await _member(c, owner, "emp@default-on.example")
        await _leave_request(c, owner, await auth_cookie(member))

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == "web_push"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1  # leave.requested is immediate: pushed with nothing ticked


async def test_a_digest_event_is_not_pushed_by_default(client_for) -> None:
    """The other half of the split, end to end rather than off the matrix."""
    t = await make_tenant("push-digest-silent")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/notifications/push/subscriptions", json=_subscription(), headers=owner
        )
        member = await _member(c, owner, "emp@digest-silent.example")
        # Self-assigned, so creating it notifies nobody: `task.assigned` is immediate and would
        # otherwise leave the row this test is looking for the absence of.
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "company_id": await default_company(c, owner),
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Iets te doen",
                    "assignee_user_id": str(t.user.id),
                },
                headers=owner,
            )
        ).json()
        res = await c.post(
            f"/api/v1/tasks/{task['id']}/comments",
            json={"body": "Een opmerking"},
            headers=await auth_cookie(member),
        )
        assert res.status_code == 201, res.text

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == "web_push"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


async def test_an_explicit_off_survives_the_default_being_on(client_for) -> None:
    """A default is what applies when nothing has been said — never an overwrite of a decision."""
    t = await make_tenant("push-explicit-off")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "push_events": [
                    {"event_type": "leave.requested", "enabled": False, "digest": "immediate"}
                ]
            },
            headers=owner,
        )
        await c.post(
            "/api/v1/notifications/push/subscriptions", json=_subscription(), headers=owner
        )
        member = await _member(c, owner, "emp@explicit-off.example")
        await _leave_request(c, owner, await auth_cookie(member))

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == "web_push"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


async def test_one_delivery_row_per_recipient_however_many_devices(client_for) -> None:
    """Per recipient, not per device: the cadence is the person's, the devices are how you
    reach them. A row per device would turn one daily digest into three messages."""
    t = await make_tenant("push-one-row")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "push_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "immediate"}
                ]
            },
            headers=owner,
        )
        for n in range(3):
            res = await c.post(
                "/api/v1/notifications/push/subscriptions",
                json=_subscription(f"https://fcm.googleapis.com/fcm/send/dev{n}"),
                headers=owner,
            )
            assert res.status_code == 201, res.text
        member = await _member(c, owner, "emp@one-row.example")
        await _leave_request(c, owner, await auth_cookie(member))

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == "web_push"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "pending"


async def test_no_device_means_no_delivery_row(client_for) -> None:
    """Otherwise every event leaves a row for every colleague who never granted permission, and
    the sweep spends its window discovering that."""
    t = await make_tenant("push-no-device")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "push_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "immediate"}
                ]
            },
            headers=owner,
        )
        member = await _member(c, owner, "emp@no-device.example")
        await _leave_request(c, owner, await auth_cookie(member))

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == "web_push"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


async def test_fanout_query_count_does_not_grow_with_devices(client_for, count_queries) -> None:
    """The shape this pins is invisible in the JSON: one query at three devices and one per
    device at three hundred passes every functional test either way (docs/PERFORMANCE.md)."""
    t = await make_tenant("push-budget")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "push_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "immediate"}
                ]
            },
            headers=owner,
        )
        for n in range(6):
            await c.post(
                "/api/v1/notifications/push/subscriptions",
                json=_subscription(f"https://fcm.googleapis.com/fcm/send/b{n}"),
                headers=owner,
            )
        member = await _member(c, owner, "emp@budget.example")
        mh = await auth_cookie(member)
        with count_queries() as counter:
            await _leave_request(c, owner, mh)

    # The channel spends exactly two: the preference resolution and the "who has a device"
    # lookup. Both are batched over the whole recipient set.
    channel_queries = [
        s
        for s in counter.statements
        if "push_subscriptions" in s or ("notification_preferences" in s and "web_push" in s)
    ]
    assert len(channel_queries) <= 3, "\n".join(channel_queries)


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #
@pytest.fixture
def captured_pushes(monkeypatch):  # noqa: ANN001, ANN201
    """Capture what would leave, and let a test decide how each endpoint answers."""
    sent: list[tuple[str, dict]] = []
    outcomes: dict[str, Exception] = {}

    async def fake_send(endpoint, *, p256dh, auth, payload, keys, subject, **kwargs):  # noqa: ANN001
        sent.append((endpoint, payload))
        if endpoint in outcomes:
            raise outcomes[endpoint]

    monkeypatch.setattr(webpush_core, "send", fake_send)
    monkeypatch.setattr(webpush_module.webpush, "send", fake_send)
    return sent, outcomes


async def _sweep(org_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        org = await session.get(Org, org_id)
        await webpush_module.dispatch_webpush_deliveries(session, org)
        await session.commit()


async def test_sweep_sends_one_bundle_to_every_device(client_for, captured_pushes) -> None:
    """Two events, one recipient, two devices → one message, delivered twice."""
    sent, _ = captured_pushes
    t = await make_tenant("push-sweep")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "push_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "immediate"}
                ]
            },
            headers=owner,
        )
        for n in range(2):
            await c.post(
                "/api/v1/notifications/push/subscriptions",
                json=_subscription(f"https://fcm.googleapis.com/fcm/send/s{n}"),
                headers=owner,
            )
        member = await _member(c, owner, "emp@sweep.example")
        mh = await auth_cookie(member)
        await _leave_request(c, owner, mh, 0)
        await _leave_request(c, owner, mh, 1)

    await _sweep(t.org.id)

    assert len(sent) == 2  # two devices, one bundle each
    endpoints = {endpoint for endpoint, _ in sent}
    assert endpoints == {
        "https://fcm.googleapis.com/fcm/send/s0",
        "https://fcm.googleapis.com/fcm/send/s1",
    }
    payload = sent[0][1]
    assert payload["count"] == 2
    # The sentence, not a raw event type or an i18n key (#236).
    assert "leave.requested" not in payload["body"]
    assert "notifications.event" not in payload["body"]
    # A digest opens the inbox: there is no single record it is about.
    assert payload["url"].endswith("/notifications")

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == "web_push"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2 and all(r.status == "sent" for r in rows)


async def test_a_gone_device_is_pruned_without_burning_an_attempt(
    client_for, captured_pushes
) -> None:
    """A 410 is a retired browser, not a delivery failure.

    Two things are asserted together because either alone hides the bug: the row is deleted, and
    the *other* device's bundle still settles as sent. Counting the 410 as a failure would keep
    the bundle pending and eventually exhaust its attempts against a device that no longer exists.
    """
    sent, outcomes = captured_pushes
    dead = "https://fcm.googleapis.com/fcm/send/dead"
    live = "https://fcm.googleapis.com/fcm/send/live"
    outcomes[dead] = webpush_core.WebPushError("gone", status_code=410, gone=True)

    t = await make_tenant("push-gone")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "push_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "immediate"}
                ]
            },
            headers=owner,
        )
        for endpoint in (dead, live):
            await c.post(
                "/api/v1/notifications/push/subscriptions",
                json=_subscription(endpoint),
                headers=owner,
            )
        member = await _member(c, owner, "emp@gone.example")
        await _leave_request(c, owner, await auth_cookie(member))

    await _sweep(t.org.id)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        remaining = (await session.execute(select(PushSubscription))).scalars().all()
        assert [row.endpoint for row in remaining] == [live]

        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == "web_push"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "sent", "one live device is a delivery"
        assert rows[0].attempts == 1


async def test_a_failing_device_keeps_the_bundle_pending_with_the_error(
    client_for, captured_pushes
) -> None:
    sent, outcomes = captured_pushes
    endpoint = "https://fcm.googleapis.com/fcm/send/flaky"
    outcomes[endpoint] = webpush_core.WebPushError("push service returned 503", status_code=503)

    t = await make_tenant("push-retry")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "push_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "immediate"}
                ]
            },
            headers=owner,
        )
        await c.post(
            "/api/v1/notifications/push/subscriptions",
            json=_subscription(endpoint),
            headers=owner,
        )
        member = await _member(c, owner, "emp@retry.example")
        await _leave_request(c, owner, await auth_cookie(member))

    await _sweep(t.org.id)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == "web_push"
                    )
                )
            )
            .scalars()
            .one()
        )
        assert row.status == "pending"  # retried, not abandoned
        assert row.attempts == 1
        assert "503" in (row.last_error or "")
        # The subscription survives: a 503 says nothing about the device.
        assert len((await session.execute(select(PushSubscription))).scalars().all()) == 1


async def test_a_single_notification_deep_links_to_its_record(
    client_for, captured_pushes
) -> None:
    """One item opens the thing it is about; only a digest falls back to the inbox."""
    sent, _ = captured_pushes
    t = await make_tenant("push-deeplink")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "push_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "immediate"}
                ]
            },
            headers=owner,
        )
        await c.post(
            "/api/v1/notifications/push/subscriptions", json=_subscription(), headers=owner
        )
        member = await _member(c, owner, "emp@deeplink.example")
        await _leave_request(c, owner, await auth_cookie(member))

    await _sweep(t.org.id)

    assert len(sent) == 1
    payload = sent[0][1]
    assert payload["count"] == 1
    assert "/leave/team?request=" in payload["url"]


async def test_test_push_reports_how_many_devices_took_it(client_for, captured_pushes) -> None:
    """The mirror of the channel test-send (#17): "did connecting this browser work?" is not a
    question the settings screen can answer by itself."""
    sent, outcomes = captured_pushes
    dead = "https://fcm.googleapis.com/fcm/send/t-dead"
    outcomes[dead] = webpush_core.WebPushError("gone", status_code=410, gone=True)

    t = await make_tenant("push-test")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for endpoint in ("https://fcm.googleapis.com/fcm/send/t-live", dead):
            await c.post(
                "/api/v1/notifications/push/subscriptions",
                json=_subscription(endpoint),
                headers=headers,
            )
        res = await c.post("/api/v1/notifications/push/test", headers=headers)
        assert res.status_code == 200, res.text
        assert res.json() == {"ok": True, "delivered": 1, "error": None}

        # A test is as good a moment as a real send to learn a device is retired.
        listed = (
            await c.get("/api/v1/notifications/push/subscriptions", headers=headers)
        ).json()
        assert len(listed) == 1


async def test_unsubscribe_drops_only_the_callers_own_endpoint(client_for) -> None:
    t = await make_tenant("push-unsub")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "emp@unsub.example")
        mh = await auth_cookie(member)
        theirs = "https://fcm.googleapis.com/fcm/send/theirs"
        await c.post(
            "/api/v1/notifications/push/subscriptions",
            json=_subscription(theirs),
            headers=mh,
        )
        # The owner naming somebody else's endpoint changes nothing — scoped to the caller.
        gone = await c.post(
            "/api/v1/notifications/push/unsubscribe",
            json={"endpoint": theirs},
            headers=owner,
        )
        assert gone.status_code == 204
        theirs_still = await c.get("/api/v1/notifications/push/subscriptions", headers=mh)
        assert len(theirs_still.json()) == 1

        # Unsubscribing twice is not an error: the browser threw the subscription away either way.
        for _ in range(2):
            res = await c.post(
                "/api/v1/notifications/push/unsubscribe", json={"endpoint": theirs}, headers=mh
            )
            assert res.status_code == 204
        assert (await c.get("/api/v1/notifications/push/subscriptions", headers=mh)).json() == []
