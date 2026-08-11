"""Uptime Kuma Socket.IO client, per **tenant** instance (docs/UPTIME.md §2).

Uptime Kuma has no REST API: at 2.5.0 its entire HTTP surface is ``/api/entry-page``, six badge
routes and ``/metrics``. Every monitor, group, tag and notification write is a Socket.IO event,
so this file is not a convenience layer over an API — it *is* the API.

It is written here rather than taken from PyPI because the published wrapper
(``uptime-kuma-api``) has not been touched since April 2024 and caps at Uptime Kuma 1.23, and
the only 2.x-capable fork fails the most basic call against the version it claims to support:
``add`` needs ``conditions`` (a 2.x ``NOT NULL`` column with no default) and returns the new id
under ``monitorID``, not the documented ``monitorId``. Both were observed, not inferred.

Two deliberate departures from that wrapper's design, both earned by measurement:

* **A read is fenced by an ack, and the ack is not always where the answer is.** The wrapper
  reads pushed lists and then sleeps a fixed ``wait_events`` (0.2 s) "because there is no way to
  determine when the last message of a certain type has arrived" — a guess that silently
  truncates a large list. Refusing to guess was right; assuming the ack *carries* the list was
  not, and it cost this module every monitor it was supposed to mirror. Measured against a live
  server: ``getMonitorList`` answers a bare ``{"ok": true}`` and **pushes** ``monitorList``
  separately, ``getSettings`` answers ``{"ok": true, "data": …}`` while the channels arrive as a
  pushed ``notificationList``, and only ``getTags`` really does answer in its ack. So a list read
  waits for *its own named event* — never a sleep, never a fixed delay — with the ack as the
  fence that says it was sent and surfaces a refusal as a typed error. See :meth:`_await_push`.
* **Nothing connects in a constructor.** Connecting is what can fail, block, and need a
  ``finally``; a constructor that dials is a constructor that raises.

Rules that do not bend:

* **The credential never reaches a log line, an exception message, or a response.** Only Kuma's
  own text does, and :mod:`.errors` carries nothing else.
* **Nothing is deleted at Kuma that schakl did not create.** Every destructive call takes an id
  this module stored earlier.
* **Read-then-write, never blind write.** A monitor round-trips 119 keys against the 16 a create
  sends; :func:`merge_monitor` is the only sanctioned way to build an edit payload.
* **The network is off in tests.** :data:`_connector` is the one seam; unset, every call dials
  the real socket and a test that forgot the fake fails loudly rather than reaching a tenant's
  instance.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import socketio

from app.modules.uptime import errors

logger = logging.getLogger("schakl.uptime")

#: Kuma is a dependency of a *screen* and of a cron, not of a page load. Ten seconds is the
#: reference client's default and is generous for a handshake that measured 5–9 ms on a LAN;
#: it is the sick-instance case this bounds, which is exactly when somebody is watching.
DEFAULT_TIMEOUT = 10.0

#: How long to wait for the unauthenticated ``info`` event that proves the target is Uptime Kuma
#: (§5, gate 3). Measured at well under 100 ms; a target that has not said it in two seconds is
#: not going to.
IDENTITY_TIMEOUT = 2.0

#: How long a list read waits for the event that carries its answer, once the ack has confirmed
#: the server accepted the request. This is a wait on a *named event*, not the fixed sleep the
#: published wrapper uses: it ends the moment the list arrives, and only the ceiling is a guess.
#: Generous because the far end serialises every monitor it has into one frame.
PUSH_TIMEOUT = 10.0

#: The lists Uptime Kuma delivers by pushing rather than by answering. Registered as handlers at
#: connect time because ``monitorList`` and ``notificationList`` are both sent *unprompted* right
#: after authentication — a handler installed later would miss the copy already delivered.
_PUSH_EVENTS = ("monitorList", "notificationList")

#: The oldest instance this module will speak to. 1.21.3 is where the published wrapper's own
#: support began and where the socket vocabulary used here settled.
MIN_VERSION = (1, 21, 3)

#: 2.x refuses an ``add`` whose ``conditions`` is NULL — a ``NOT NULL`` column with no default,
#: and the single most likely thing to break a payload built from 1.x documentation.
REQUIRED_ON_CREATE: dict[str, Any] = {"conditions": []}

#: Kuma's own answer when a stored token no longer verifies. An i18n key, so it is stable across
#: locales in a way the rate limiter's bare English sentence is not.
_REAUTH_MSG = "authInvalidToken"

#: The rate limiter is the one refusal 2.x still sends as English prose with no ``msgi18n``.
_RATE_LIMIT_MARKERS = ("too frequently", "rate limit")

_CREDENTIAL_MARKERS = ("incorrect username or password", "invalid username or password")

#: Test seam — a factory returning a ``socketio.Client``-shaped object. Never set in production.
_connector: Callable[..., Any] | None = None


def set_connector(factory: Callable[..., Any] | None) -> None:
    """Install (or clear) the factory every client dials through. Tests only."""
    global _connector
    _connector = factory


def normalise_base_url(url: str) -> str:
    """The instance URL with any trailing slash and query dropped, scheme, host and subpath kept.

    A subpath is kept because an agency behind a reverse proxy runs Kuma at
    ``https://host/kuma/`` — but keeping it in the URL is *not* enough to reach it. See
    :func:`socketio_path_for`.
    """
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        raise ValueError("uptime instance url must be absolute")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def socketio_path_for(base_url: str) -> str:
    """The ``socketio_path`` that actually reaches this instance's socket.io endpoint.

    **python-socketio discards the path of the URL it is handed.** ``_get_engineio_url`` rebuilds
    the request as ``{scheme}://{netloc}/{socketio_path}/``, so connecting to
    ``https://host/kuma/socket.io/`` really requests ``https://host/socket.io/``. Observed, not
    inferred: a client pointed at ``http://localhost:3011/definitely-not-kuma`` connected happily
    to the Uptime Kuma at the root.

    That is worse than a connection failure. An agency running Kuma on a subpath would either
    fail for no visible reason or — on a host serving more than one thing — silently reach a
    *different* instance and mirror the wrong monitors. So the subpath is folded into the
    socket.io path explicitly, which is the only parameter python-socketio honours.
    """
    path = urlsplit(base_url).path.strip("/")
    return f"{path}/socket.io" if path else "socket.io"


def origin_of(base_url: str) -> str:
    """Scheme and host only — what python-socketio is actually going to dial."""
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _version_tuple(version: str | None) -> tuple[int, ...]:
    """``"2.5.0"`` → ``(2, 5, 0)``; a beta or an unparseable string degrades rather than raises."""
    if not version:
        return ()
    head = version.split("-", 1)[0]
    out: list[int] = []
    for part in head.split("."):
        if not part.isdigit():
            break
        out.append(int(part))
    return tuple(out)


def merge_monitor(observed: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """An edit payload: everything Kuma just gave us, with our fields written over it.

    This is the only sanctioned way to build one. ``editMonitor`` takes the whole monitor, and a
    live 2.5.0 returns **119 keys against the 16 a create sends** — so a payload rebuilt from the
    fields this module models would silently reset a hundred of them, including every field
    belonging to a monitor type we do not know about yet.

    Refuses a snapshot that has been through :mod:`.redaction`: those carry ``{"set", "fp"}``
    where a secret was, and writing that back would replace a client's database password with a
    JSON object. A write path must start from the *unredacted* read.
    """
    for field, value in observed.items():
        if isinstance(value, dict) and set(value) == {"set", "fp"}:
            raise ValueError(
                f"merge_monitor was handed a redacted snapshot ({field!r}); "
                "re-read the monitor before writing"
            )
    return {**observed, **changes}


class UptimeKumaClient:
    """One Socket.IO conversation with one Uptime Kuma instance.

    Connect-per-operation and disconnect in a ``finally`` — use it as a context manager. A
    long-lived socket is wrong here: the API rolls ``start-first`` on two replicas, so a
    persistent connection means two of them with two opinions about what is current, and a proxy
    in front of the instance idles it out regardless.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        ssl_verify: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = normalise_base_url(base_url)
        #: Sent on the socket.io handshake. This is the entire tunnel feature (§5): a Cloudflare
        #: Access service token is two headers and nothing else. Never logged.
        self._headers = dict(headers or {})
        self._ssl_verify = ssl_verify
        self._timeout = timeout
        self._sio: Any | None = None
        self._info: list[dict[str, Any]] = []
        self._authenticated = False
        #: The last payload seen for each pushed list, tagged with an arrival number. The tag is
        #: what lets a read tell *this call's* answer from the copy login already delivered —
        #: without it, a stale list would satisfy a wait that its own request never answered.
        self._pushes: dict[str, tuple[int, Any]] = {}
        self._push_count = 0

    # ---------------------------------------------------------------- connection

    def __enter__(self) -> UptimeKumaClient:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        """Open the socket and wait for proof that the far end is Uptime Kuma.

        The proof is *an ``info`` event arriving*, not an HTTP status and not a version. A
        half-installed 2.x serves its SPA's HTML with HTTP 200 on every path including
        ``/socket.io/``, and 2.x withholds ``version`` from unauthenticated clients — so status
        proves nothing and the version cannot be the gate. The version floor is checked later,
        after authentication, by :meth:`require_supported_version`.
        """
        factory = _connector or socketio.Client
        sio = factory(ssl_verify=self._ssl_verify)
        sio.on("info", self._on_info)
        for event in _PUSH_EVENTS:
            sio.on(event, self._push_handler(event))
        try:
            # The origin and the socket.io path are passed separately on purpose — the path of
            # the URL is discarded by python-socketio, so a subpath instance is only reachable
            # through ``socketio_path`` (see :func:`socketio_path_for`).
            sio.connect(
                origin_of(self.base_url),
                socketio_path=socketio_path_for(self.base_url),
                wait_timeout=self._timeout,
                headers=self._headers,
            )
        except Exception as exc:  # noqa: BLE001 — the transport raises many shapes
            raise self._connect_failure(exc) from exc
        self._sio = sio

        deadline = time.monotonic() + IDENTITY_TIMEOUT
        while not self._info and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self._info:
            self.close()
            raise errors.NotUptimeKuma("no info event")

    def close(self) -> None:
        """Disconnect. Safe to call twice, and never raises — it runs in a ``finally``."""
        sio, self._sio = self._sio, None
        if sio is None:
            return
        try:
            sio.disconnect()
        except Exception:  # noqa: BLE001
            logger.debug("uptime: disconnect failed", exc_info=True)

    def _connect_failure(self, exc: Exception) -> errors.UptimeKumaError:
        """Classify a handshake failure without ever quoting the URL or the headers.

        A gateway in front of Kuma (Access, a proxy, a WAF) refuses at the HTTP layer and is
        otherwise indistinguishable from the host being down — opposite fixes, so they get
        opposite classes.
        """
        text = str(exc)
        lowered = text.lower()
        if any(m in lowered for m in ("403", "401", "forbidden", "unauthorized")):
            return errors.GatewayRefused(text[:200])
        return errors.Unreachable(text[:200] or type(exc).__name__)

    def _on_info(self, data: dict[str, Any]) -> None:
        """``info`` is emitted **twice** — once before authentication without ``version``, once
        after with it. Keep both; the version is read from the newest that carries one."""
        if isinstance(data, dict):
            self._info.append(data)

    def _push_handler(self, event: str) -> Callable[[Any], None]:
        """Record a pushed list under its event name, newest wins."""

        def handle(data: Any) -> None:
            self._push_count += 1
            self._pushes[event] = (self._push_count, data)

        return handle

    def _await_push(self, event: str, *, after: int, timeout: float) -> Any | None:
        """Wait for a ``event`` push newer than arrival number ``after``. ``None`` on timeout.

        Polls rather than blocking on a condition variable because the transport delivers on its
        own thread and this client is otherwise synchronous; the loop is bounded and exits the
        instant the list lands, so the cost is one comparison per 10 ms of a wait that normally
        ends in single-digit milliseconds.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entry = self._pushes.get(event)
            if entry is not None and entry[0] > after:
                return entry[1]
            time.sleep(0.01)
        return None

    def _seen(self, event: str) -> int:
        """The arrival number of the newest ``event`` push, or ``-1`` if none has come."""
        entry = self._pushes.get(event)
        return entry[0] if entry is not None else -1

    def _call_for_list(self, event: str, push_event: str, ack_key: str, *args: Any) -> Any:
        """Ask for a list, and take the answer from wherever this server puts it.

        Three outcomes in a deliberate order. The **ack carries it** — the shape 1.x uses for
        some reads and the shape this module wrongly assumed for all of them; taking it here
        keeps a version that answers directly working with no branch elsewhere. Otherwise the
        **push that this call provoked**, which is the live behaviour of both 1.23 and 2.5.
        Otherwise a **copy delivered earlier** (login pushes both lists unprompted), which is a
        fallback and not the happy path — a server that acks without pushing would otherwise
        strand a read that had a perfectly good answer already in hand.

        Never an empty list on a timeout. Returning ``[]`` for "nobody answered" is the exact
        bug this method exists to have fixed: it is indistinguishable from a real empty list, so
        a broken read reads as an empty instance and no error is ever raised.
        """
        before = self._seen(push_event)
        result = self._call(event, *args)
        if isinstance(result, dict) and ack_key in result:
            return result[ack_key]
        pushed = self._await_push(push_event, after=before, timeout=PUSH_TIMEOUT)
        if pushed is not None:
            return pushed
        entry = self._pushes.get(push_event)
        if entry is not None:
            return entry[1]
        raise errors.Unreachable(f"{event} produced no {push_event}")

    @property
    def server_version(self) -> str | None:
        """What the instance says it runs, or ``None`` while unauthenticated."""
        for info in reversed(self._info):
            version = info.get("version")
            if version:
                return str(version)
        return None

    def require_supported_version(self) -> str:
        """Refuse an instance below :data:`MIN_VERSION`. Only meaningful once authenticated."""
        version = self.server_version
        parsed = _version_tuple(version)
        if parsed and parsed < MIN_VERSION:
            raise errors.VersionUnsupported(
                f"uptime kuma {version} is below the supported floor", version=version
            )
        return version or ""

    # ------------------------------------------------------------------- calling

    def _call(self, event: str, *args: Any) -> Any:
        """One acknowledged Socket.IO call, with Kuma's refusal mapped to a typed error.

        Kuma answers ``{"ok": False, "msg": …}`` rather than raising, and 2.x sends ``msg`` as an
        **i18n key** with ``msgi18n: True`` — except the rate limiter, which still sends English
        prose with no flag. Nothing here branches on prose except where Kuma leaves no choice.
        """
        sio = self._sio
        if sio is None:
            raise errors.Unreachable("not connected")
        payload = args[0] if len(args) == 1 else (tuple(args) if args else None)
        try:
            if payload is None:
                result = sio.call(event, timeout=self._timeout)
            else:
                result = sio.call(event, payload, timeout=self._timeout)
        except socketio.exceptions.TimeoutError as exc:
            raise errors.Unreachable(f"timed out waiting for {event}") from exc

        if not isinstance(result, dict):
            return result
        if result.get("ok") is False:
            raise self._refusal(result)
        result.pop("ok", None)
        return result

    def _refusal(self, result: dict[str, Any]) -> errors.UptimeKumaError:
        msg = str(result.get("msg") or "")
        i18n = bool(result.get("msgi18n"))
        lowered = msg.lower()
        if msg == _REAUTH_MSG:
            return errors.ReauthRequired(msg, i18n=True)
        if any(m in lowered for m in _RATE_LIMIT_MARKERS):
            return errors.RateLimited(msg, i18n=i18n)
        if any(m in lowered for m in _CREDENTIAL_MARKERS):
            return errors.CredentialsRejected(msg, i18n=i18n)
        return errors.UptimeKumaError(msg or "uptime kuma refused the call", i18n=i18n)

    # ---------------------------------------------------------------------- auth

    def needs_setup(self) -> bool:
        """Whether this instance has no account yet — a brand-new Kuma nobody has claimed."""
        return bool(self._call("needSetup"))

    def enrol(self, username: str, password: str, *, totp: str | None = None) -> str:
        """Authenticate once with a password and return the token to store instead of it.

        This is the **only** method that takes a password, and its result is what makes that
        acceptable: Kuma's JWT carries ``{username, h, iat}`` and no ``exp``, and
        ``loginByToken`` re-derives ``h`` from the live password hash — so the token is
        long-lived, revoked by a password change or a deactivated user, and holds neither the
        password nor a second factor. Caller stores the return value and discards its inputs.
        """
        result = self._call(
            "login", {"username": username, "password": password, "token": totp or ""}
        )
        if isinstance(result, dict) and result.get("tokenRequired"):
            raise errors.TotpRequired("two-factor code required")
        token = (result or {}).get("token")
        if not token:
            # 2FA on with a *wrong* code answers neither a token nor `tokenRequired`.
            raise (
                errors.TotpRejected("two-factor code rejected")
                if totp
                else errors.CredentialsRejected("login did not return a token")
            )
        self._authenticated = True
        self._settle_info()
        return str(token)

    def authenticate(self, token: str) -> None:
        """Resume with a stored token. Raises :class:`~.errors.ReauthRequired` once it is dead."""
        self._call("loginByToken", token)
        self._authenticated = True
        self._settle_info()

    def _settle_info(self) -> None:
        """Wait briefly for the post-authentication ``info`` that carries ``version``.

        The only place this client waits on a push rather than an ack, because Kuma offers no
        call for it. Bounded and best-effort: a missing version costs the floor check, not the
        connection.
        """
        deadline = time.monotonic() + 1.0
        while self.server_version is None and time.monotonic() < deadline:
            time.sleep(0.01)

    # ------------------------------------------------------------------ monitors

    def list_monitors(self) -> dict[int, dict[str, Any]]:
        """Every monitor, by id — **groups included**, since a group is a monitor here.

        The ack for ``getMonitorList`` is a bare ``{"ok": true}``; the monitors arrive as a
        pushed ``monitorList`` keyed by id-as-string. Reading the ack instead returned ``{}``
        against a live instance holding 34 monitors, with no error anywhere: the sync reported
        success, created nothing, and the module looked connected and empty.
        """
        raw = self._call_for_list("getMonitorList", "monitorList", "monitorList")
        if not isinstance(raw, dict):
            raise errors.UptimeKumaError("uptime kuma sent a monitor list of the wrong shape")
        return {int(k): v for k, v in raw.items()}

    def get_monitor(self, monitor_id: int) -> dict[str, Any]:
        """One monitor, unredacted — the payload a later edit must round-trip."""
        return self._call("getMonitor", int(monitor_id))["monitor"]

    def add_monitor(self, payload: dict[str, Any]) -> int:
        """Create a monitor and return Kuma's id for it.

        ``conditions`` is forced **only on 2.x**, which declares it ``NOT NULL`` with no default
        and answers a raw constraint violation without it. On 1.x there is no such column, and
        the create payload is imported onto the row wholesale — so sending it there is not a
        harmless extra key, it is an unknown column against the tenant's own database.

        Unknown version omits it, and that asymmetry is deliberate: omitting on 2.x fails
        cleanly, loudly and reversibly, naming the column in the error. The opposite mistake
        writes to a schema. The id comes back as ``monitorID`` on 2.x and ``monitorId`` on 1.x,
        and both are read — a rename in a return key is what a version shim exists to absorb.
        """
        extra = REQUIRED_ON_CREATE if _version_tuple(self.server_version)[:1] >= (2,) else {}
        result = self._call("add", {**extra, **payload})
        monitor_id = result.get("monitorID", result.get("monitorId"))
        if monitor_id is None:
            raise errors.UptimeKumaError("uptime kuma did not return a monitor id")
        return int(monitor_id)

    def edit_monitor(self, payload: dict[str, Any]) -> None:
        """Write a monitor back. ``payload`` must come from :func:`merge_monitor`."""
        if "id" not in payload:
            raise ValueError("edit_monitor needs the monitor's own id")
        self._call("editMonitor", payload)

    def pause_monitor(self, monitor_id: int) -> None:
        self._call("pauseMonitor", int(monitor_id))

    def resume_monitor(self, monitor_id: int) -> None:
        self._call("resumeMonitor", int(monitor_id))

    def delete_monitor(self, monitor_id: int) -> None:
        """Delete a monitor. The id must be one this module stored — never a name match."""
        self._call("deleteMonitor", int(monitor_id))

    # ------------------------------------------------------- tags, notifications

    def list_tags(self) -> list[dict[str, Any]]:
        result = self._call("getTags")
        return list(result.get("tags", []) if isinstance(result, dict) else result or [])

    def list_notifications(self) -> list[dict[str, Any]]:
        """The instance's notification channels — read so a monitor can be assigned to them.

        This module does not manage them: an agency configuring Slack in Kuma is doing the right
        thing, and Kuma is better at delivery than we would be.

        Push-only, and unlike the monitor list **nothing re-requests it**: ``notificationList``
        is sent once, unprompted, after authentication, and no event asks for it again. So this
        reads what login delivered rather than calling ``getSettings`` — whose ack carries
        ``data`` (the instance's own settings) and never the channels, which is what made this
        return ``[]`` on every instance that had them.
        """
        pushed = self._await_push("notificationList", after=-1, timeout=PUSH_TIMEOUT)
        if pushed is None:
            # Not an error: a caller wanting channels can act on "none", and refusing the whole
            # operation over a nicety would be worse. Logged so it is not silent.
            logger.debug("uptime: no notificationList push arrived")
            return []
        return list(pushed or [])
