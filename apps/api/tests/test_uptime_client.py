"""The vendored Socket.IO client and the redaction layer (docs/UPTIME.md §2, §4, §15).

No network: every test drives :class:`tests.uptime_fake.FakeKuma` through the module's single
connector seam. A test that forgot to install it would dial a real socket and fail loudly, which
is the point of there being exactly one seam.
"""

from __future__ import annotations

import pytest

from app.modules.uptime import client as kuma_client
from app.modules.uptime import errors
from app.modules.uptime.client import (
    UptimeKumaClient,
    merge_monitor,
    normalise_base_url,
    origin_of,
    socketio_path_for,
)
from app.modules.uptime.redaction import SECRET_FIELDS, redact_monitor, secret_drift
from tests.uptime_fake import FakeKuma


@pytest.fixture
def kuma(monkeypatch) -> FakeKuma:
    fake = FakeKuma()
    monkeypatch.setattr(kuma_client, "_connector", fake.connector)
    return fake


# --------------------------------------------------------------------- url handling


@pytest.mark.parametrize(
    ("url", "origin", "path"),
    [
        ("https://kuma.example.nl", "https://kuma.example.nl", "socket.io"),
        ("https://kuma.example.nl/", "https://kuma.example.nl", "socket.io"),
        ("https://host/kuma/", "https://host", "kuma/socket.io"),
        ("http://host:3011/a/b/", "http://host:3011", "a/b/socket.io"),
    ],
)
def test_subpath_is_folded_into_socketio_path(url: str, origin: str, path: str) -> None:
    """python-socketio **discards the path of the URL it is handed** and rebuilds the request
    from ``socketio_path``. Observed against a live instance: a client pointed at
    ``/definitely-not-kuma`` connected happily to the Kuma at the root.

    Left alone, an agency running Kuma behind a reverse proxy on a subpath would either fail for
    no visible reason or — on a host serving more than one thing — silently reach a *different*
    instance and mirror the wrong client's monitors.
    """
    normalised = normalise_base_url(url)
    assert origin_of(normalised) == origin
    assert socketio_path_for(normalised) == path


def test_the_subpath_actually_reaches_the_socket(kuma: FakeKuma) -> None:
    with UptimeKumaClient("https://host/kuma/") as c:
        c.authenticate(kuma.token)
    sock = kuma.connections[0]
    assert sock.url == "https://host"
    assert sock.socketio_path == "kuma/socket.io"


def test_relative_url_is_refused() -> None:
    with pytest.raises(ValueError):
        normalise_base_url("kuma.example.nl")


# ------------------------------------------------------------------ identity gate


def test_a_target_that_never_sends_info_is_not_uptime_kuma(kuma: FakeKuma) -> None:
    """A half-installed 2.x serves its SPA with HTTP 200 on every path, ``/socket.io/``
    included, so "something answered" proves nothing. Proof is an ``info`` event."""
    kuma.silent = True
    with pytest.raises(errors.NotUptimeKuma):
        UptimeKumaClient("https://kuma.example.nl", timeout=0.2).connect()


def test_a_refused_handshake_is_unreachable_not_a_credential_problem(kuma: FakeKuma) -> None:
    kuma.unreachable = True
    with pytest.raises(errors.Unreachable):
        UptimeKumaClient("https://kuma.example.nl").connect()


def test_a_gateway_refusal_is_its_own_class(kuma: FakeKuma) -> None:
    """Access or a proxy refusing is indistinguishable from the host being down at the socket
    layer, and needs the opposite fix — a service token, not a server."""
    kuma.gateway_refused = True
    with pytest.raises(errors.GatewayRefused):
        UptimeKumaClient("https://kuma.example.nl").connect()


def test_a_failed_connect_leaves_no_socket_open(kuma: FakeKuma) -> None:
    kuma.silent = True
    with pytest.raises(errors.NotUptimeKuma):
        UptimeKumaClient("https://kuma.example.nl", timeout=0.2).connect()
    assert kuma.open_connections == 0


# ------------------------------------------------------------------------- auth


def test_version_is_withheld_until_authenticated(kuma: FakeKuma) -> None:
    """2.x sends ``info`` twice and only the second carries ``version``.

    Reading the first and expecting a version — which is what a naive ``api.version`` does —
    yields ``None`` on every 2.x instance, which is why the identity gate and the version floor
    are two checks at two moments.
    """
    with UptimeKumaClient("https://kuma.example.nl") as c:
        assert c.server_version is None
        c.authenticate(kuma.token)
        assert c.server_version == "2.5.0"


def test_enrol_returns_a_token_and_never_needs_the_password_again(kuma: FakeKuma) -> None:
    with UptimeKumaClient("https://kuma.example.nl") as c:
        token = c.enrol("admin", "secret")
    assert token == kuma.token
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(token)
        assert c.require_supported_version() == "2.5.0"


def test_a_revoked_token_is_reauth_not_rejected_credentials(kuma: FakeKuma) -> None:
    """The pair that must never collapse: same shape, opposite instruction."""
    kuma.token_revoked = True
    with UptimeKumaClient("https://kuma.example.nl") as c:
        with pytest.raises(errors.ReauthRequired):
            c.authenticate(kuma.token)


def test_a_wrong_password_is_rejected_credentials(kuma: FakeKuma) -> None:
    with UptimeKumaClient("https://kuma.example.nl") as c:
        with pytest.raises(errors.CredentialsRejected):
            c.enrol("admin", "wrong")


def test_the_rate_limiter_refuses_a_correct_password(kuma: FakeKuma) -> None:
    """Twenty per minute, instance-wide, shared with the instance's own owner. Read as "wrong
    password" it sends an admin to rotate a credential that was never wrong."""
    kuma.rate_limited = True
    with UptimeKumaClient("https://kuma.example.nl") as c:
        with pytest.raises(errors.RateLimited):
            c.enrol("admin", "secret")


def test_two_factor_is_reported_as_its_own_condition(kuma: FakeKuma) -> None:
    kuma.totp = "123456"
    with UptimeKumaClient("https://kuma.example.nl") as c:
        with pytest.raises(errors.TotpRequired):
            c.enrol("admin", "secret")
    with UptimeKumaClient("https://kuma.example.nl") as c:
        with pytest.raises(errors.TotpRejected):
            c.enrol("admin", "secret", totp="000000")
    with UptimeKumaClient("https://kuma.example.nl") as c:
        assert c.enrol("admin", "secret", totp="123456") == kuma.token


def test_an_old_instance_is_refused_by_the_version_floor(kuma: FakeKuma) -> None:
    kuma.version = "1.20.0"
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        with pytest.raises(errors.VersionUnsupported) as exc:
            c.require_supported_version()
    assert exc.value.version == "1.20.0"


def test_a_beta_version_degrades_rather_than_raising(kuma: FakeKuma) -> None:
    kuma.version = "2.0.0-beta.4"
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        assert c.require_supported_version() == "2.0.0-beta.4"


# --------------------------------------------------------------------- monitors


def test_create_forces_conditions_and_reads_the_2x_return_key(kuma: FakeKuma) -> None:
    """``conditions`` is a 2.x ``NOT NULL`` column with no default, and the new id comes back
    under ``monitorID``. Both break the published wrapper on its own claimed version."""
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        monitor_id = c.add_monitor({"type": "http", "name": "site", "url": "https://a.nl"})
    assert monitor_id == 1
    assert kuma.monitors[1]["conditions"] == []


def test_an_edit_preserves_the_fields_we_never_modelled(kuma: FakeKuma) -> None:
    """Kuma replaces the row from the payload, and a live 2.5.0 returns 119 keys against the 16
    a create sends. A payload rebuilt from known fields silently resets a hundred of them."""
    kuma.add(name="site", maxredirects=7, basic_auth_pass="pw", url="https://a.nl")
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        observed = c.get_monitor(1)
        c.edit_monitor(merge_monitor(observed, {"name": "renamed", "interval": 300}))
        after = c.get_monitor(1)
    assert after["name"] == "renamed" and after["interval"] == 300
    assert after["maxredirects"] == 7, "an unmodelled field was reset by the edit"
    assert after["basic_auth_pass"] == "pw"


def test_edit_without_an_id_is_a_programming_error(kuma: FakeKuma) -> None:
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        with pytest.raises(ValueError):
            c.edit_monitor({"name": "no id"})


def test_list_pause_resume_delete(kuma: FakeKuma) -> None:
    kuma.add(name="one")
    kuma.add(name="two")
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        assert sorted(c.list_monitors()) == [1, 2]
        c.pause_monitor(1)
        assert kuma.monitors[1]["active"] is False
        c.resume_monitor(1)
        assert kuma.monitors[1]["active"] is True
        c.delete_monitor(2)
        assert sorted(c.list_monitors()) == [1]


def test_the_monitor_list_arrives_as_a_push_and_never_in_the_ack(kuma: FakeKuma) -> None:
    """The bug this module shipped with, named so it cannot come back.

    ``getMonitorList`` answers a bare ``{"ok": True}`` and delivers the monitors as a separate
    pushed ``monitorList``. Reading the answer out of the ack returned ``{}`` against a live
    instance holding 34 monitors — with no error anywhere: the sync reported success, created
    nothing, and the screen said "connected" above an empty list.

    Both halves are asserted, because only together do they mean anything: the ack really is
    empty (so the fake is not being kind), and the client finds the monitors anyway.
    """
    kuma.add(name="one")
    kuma.add(name="two")
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        socket = kuma.connections[-1]
        assert socket.call("getMonitorList") == {"ok": True}, "the ack carries no list"
        assert sorted(c.list_monitors()) == [1, 2]


def test_a_second_read_is_not_satisfied_by_the_first_ones_push(kuma: FakeKuma) -> None:
    """A read waits for the push *its own call* provoked, not for any copy lying around.

    Both lists are pushed unprompted at login, so a read that accepted whatever had arrived
    would hand back a snapshot its request never answered — stale the moment anything changed,
    and stale in the direction that hides a monitor rather than inventing one.
    """
    kuma.add(name="one")
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        assert sorted(c.list_monitors()) == [1]
        kuma.add(name="two")
        assert sorted(c.list_monitors()) == [1, 2], "a repeat read returned the login snapshot"


def test_a_group_and_its_children_survive_the_read(kuma: FakeKuma) -> None:
    """A group **is** a monitor (``type: "group"``), and a child names it by integer ``parent``.

    The mirror rebuilds its whole hierarchy from those two fields, so a read that dropped either
    would leave every monitor looking top-level — which is indistinguishable, on the screen,
    from an agency that never grouped anything.
    """
    group_id = kuma.add_group("breik. hosting klanten")
    child_id = kuma.add(name="kuzee", parent=group_id, url="https://kuzee.com")
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        monitors = c.list_monitors()
    assert monitors[group_id]["type"] == "group"
    assert monitors[child_id]["parent"] == group_id


def test_notification_channels_come_from_the_login_push_not_from_getsettings(
    kuma: FakeKuma,
) -> None:
    """``getSettings`` acks the instance's own settings under ``data`` and never the channels.

    They arrive once, unprompted, at login, and no event re-requests them — so reading them from
    the ack answered ``[]`` on every instance that actually had any configured.
    """
    kuma.notifications = [{"id": 1, "name": "Slack", "active": True}]
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(kuma.token)
        assert [n["name"] for n in c.list_notifications()] == ["Slack"]


def test_a_1x_create_omits_the_2x_only_conditions_key(monkeypatch) -> None:
    """``conditions`` is forced on 2.x and must never be sent to a 1.x.

    1.x has no such column and ``add`` imports the payload onto the row wholesale, so the key a
    2.x demands is, one major version down, an unknown column against the tenant's own database.
    """
    fake = FakeKuma(version="1.23.17")
    monkeypatch.setattr(kuma_client, "_connector", fake.connector)
    with UptimeKumaClient("https://kuma.example.nl") as c:
        c.authenticate(fake.token)
        monitor_id = c.add_monitor({"type": "http", "name": "site", "url": "https://a.nl"})
    assert "conditions" not in fake.monitors[monitor_id]


def test_the_context_manager_always_closes(kuma: FakeKuma) -> None:
    with pytest.raises(errors.ReauthRequired):
        with UptimeKumaClient("https://kuma.example.nl") as c:
            kuma.token_revoked = True
            c.authenticate(kuma.token)
    assert kuma.open_connections == 0, "a socket survived an exception"


# --------------------------------------------------------------------- redaction


def test_secrets_never_survive_into_a_snapshot() -> None:
    monitor = {"name": "x", "basic_auth_pass": "CANARY", "radiusSecret": "OTHER", "url": "u"}
    snap = redact_monitor(monitor, salt="s")
    assert "CANARY" not in str(snap) and "OTHER" not in str(snap)
    assert snap["basic_auth_pass"]["set"] is True
    assert snap["url"] == "u", "a non-secret field was redacted"
    assert monitor["basic_auth_pass"] == "CANARY", "the caller's copy was mutated"


def test_an_unset_secret_is_distinguishable_from_a_set_one() -> None:
    snap = redact_monitor({"basic_auth_pass": None}, salt="s")
    assert snap["basic_auth_pass"] == {"set": False, "fp": None}


def test_a_changed_secret_is_detectable_without_storing_either_value() -> None:
    a = redact_monitor({"basic_auth_pass": "one"}, salt="s")
    b = redact_monitor({"basic_auth_pass": "two"}, salt="s")
    same = redact_monitor({"basic_auth_pass": "one"}, salt="s")
    assert secret_drift(a, b) == ("basic_auth_pass",)
    assert secret_drift(a, same) == ()


def test_the_salt_is_what_keeps_fingerprints_instance_local() -> None:
    """A shared salt would make one exported database a dictionary against every tenant."""
    a = redact_monitor({"basic_auth_pass": "same"}, salt="instance-a")
    b = redact_monitor({"basic_auth_pass": "same"}, salt="instance-b")
    assert a["basic_auth_pass"]["fp"] != b["basic_auth_pass"]["fp"]


def test_fingerprints_are_domain_separated_per_field() -> None:
    snap = redact_monitor({"basic_auth_pass": "same", "radiusPassword": "same"}, salt="s")
    assert snap["basic_auth_pass"]["fp"] != snap["radiusPassword"]["fp"]


def test_certificates_are_not_redacted() -> None:
    """A certificate and a CA bundle are public halves; hiding them would mask a real drift —
    somebody repointing a monitor at a different CA — to protect nothing."""
    assert "tlsCert" not in SECRET_FIELDS and "tlsCa" not in SECRET_FIELDS
    assert "tlsKey" in SECRET_FIELDS


def test_a_redacted_snapshot_can_never_be_written_back() -> None:
    """Writing ``{"set", "fp"}`` back would replace a client's database password with a JSON
    object. The write path must start from a fresh, unredacted read."""
    snap = redact_monitor({"id": 1, "basic_auth_pass": "pw"}, salt="s")
    with pytest.raises(ValueError, match="redacted"):
        merge_monitor(snap, {"name": "x"})


def test_secret_drift_refuses_to_compare_an_unredacted_snapshot() -> None:
    with pytest.raises(ValueError):
        secret_drift({"basic_auth_pass": "raw"}, redact_monitor({"basic_auth_pass": "x"}, salt="s"))
