"""A scriptable stand-in for Uptime Kuma's Socket.IO server (docs/UPTIME.md §15).

The uptime module must never touch the network in tests, and its interesting behaviour is
almost entirely *what happens when the far end disagrees with us* — a revoked token, a monitor
somebody edited in Kuma's own UI, a version too old, a target that answers but is not Kuma. That
needs a Kuma that holds state and can be told to misbehave, not a pile of one-off stubs.

It is modelled on what a **live server actually did**, because a fake that is kinder than the real
server is a fake in which the bug does not exist — the lesson `cloudflare_fake` already learned
once with pagination, and which this file then proceeded to repeat. So, faithfully and on purpose:

* **A list read is answered where the real server answers it, which is usually not the ack.**
  ``getMonitorList`` acks a bare ``{"ok": True}`` and **pushes** ``monitorList``; ``getSettings``
  acks ``{"ok": True, "data": …}`` and never carries the channels, which arrive as a
  ``notificationList`` push at login; only ``getTags`` answers in its own ack. This fake used to
  return both lists in the ack, so the module's central read was broken against every real
  instance while every test passed — the exact failure this docstring already claimed to
  prevent. Measured against 1.23.17 and consistent with 2.x.
* ``add`` **refuses a payload without ``conditions``** at 2.x (a ``NOT NULL`` column with no
  default) and returns the new id under ``monitorID``, not 1.x's ``monitorId``. At 1.x there is
  no such column, so a payload carrying one is refused as the unknown column it is.
* Refusals come back as ``{"ok": False, "msg": <i18n key>, "msgi18n": True}``, except the rate
  limiter, which still sends bare English prose with no flag.
* ``info`` is emitted **twice** — once before authentication *without* ``version``, once after
  *with* it — because that two-phase shape is what splits proof-of-identity from the version
  floor, and a fake that sends the version up front hides the whole distinction.
* A credential it rejects, it rejects **everywhere**. A fake that authenticates one call and
  refuses another is a fake in which a one-way error flag cannot be caught.
"""

from __future__ import annotations

from typing import Any

#: Kuma's own key for "this token no longer verifies" — the string the client keys
#: `ReauthRequired` off, so the fake must send exactly it.
REAUTH_MSG = "authInvalidToken"

#: The rate limiter's message. Deliberately English prose with **no** `msgi18n`, because that is
#: the one refusal 2.x did not translate.
RATE_LIMIT_MSG = "Too frequently, try again later."


class FakeKuma:
    """One fake instance. Install it with ``client.set_connector(fake.connector)``."""

    def __init__(
        self,
        *,
        version: str = "2.5.0",
        username: str = "admin",
        password: str = "secret",
        token: str = "fake-jwt-token",
    ) -> None:
        self.version = version
        self.username = username
        self.password = password
        self.token = token

        self.monitors: dict[int, dict[str, Any]] = {}
        self.tags: list[dict[str, Any]] = []
        #: Channels the agency configured in Kuma. Pushed at login, never in an ack.
        self.notifications: list[dict[str, Any]] = []
        self._next_id = 1

        # --- knobs a test turns to make the far end misbehave -----------------
        #: Refuse every handshake (host down, tunnel closed).
        self.unreachable = False
        #: Refuse the handshake at the HTTP layer, as Access or a proxy would.
        self.gateway_refused = False
        #: Answer, but never send `info` — a half-installed 2.x serving its SPA on every path.
        self.silent = False
        #: Reject the stored token: the Kuma password changed, or the user was deactivated.
        self.token_revoked = False
        #: Spend the login budget: refuses even a *correct* password.
        self.rate_limited = False
        #: Require a 2FA code on enrolment.
        self.totp: str | None = None
        #: Sockets this fake has handed out, so a test can assert they were all closed.
        self.connections: list[FakeSocket] = []

    # ------------------------------------------------------------------ helpers

    def connector(self, **_kwargs: Any) -> FakeSocket:
        sock = FakeSocket(self)
        self.connections.append(sock)
        return sock

    @property
    def open_connections(self) -> int:
        """How many sockets are still open. The client must leave none behind."""
        return sum(1 for c in self.connections if c.connected)

    #: What a *seeded* monitor looks like — the convenience a test uses to say "this already
    #: exists at Kuma". A live 2.5.0 returns ~119 keys; a handful of the ones that matter is
    #: enough to prove a merge preserves what it was not asked about.
    SEED_DEFAULTS: dict[str, Any] = {
        "type": "http",
        "url": "https://example.com",
        "interval": 60,
        "maxretries": 1,
        "active": True,
        "parent": None,
        "accepted_statuscodes": ["200-299"],
        "maxredirects": 10,
        "basic_auth_pass": None,
        "conditions": [],
    }

    def add(self, **fields: Any) -> int:
        """Seed a monitor directly — how a test says "this already exists at Kuma"."""
        return self._store({**self.SEED_DEFAULTS, **fields})

    def add_group(self, name: str, **fields: Any) -> int:
        """Seed a group.

        A group **is** a monitor with ``type: "group"`` — Kuma has no group entity, only
        ``MonitorType.GROUP`` and an integer ``parent`` on the children. Its ``url`` is the bare
        ``"https://"`` a live instance really stores, because a group watches nothing and a fake
        that leaves it blank hides the mirror having to know that.
        """
        return self._store(
            {**self.SEED_DEFAULTS, "type": "group", "url": "https://", "name": name, **fields}
        )

    def is_v2(self) -> bool:
        """Whether this fake is a 2.x. Decides the ``conditions`` column's existence."""
        head = self.version.split("-", 1)[0].split(".")[0]
        return head.isdigit() and int(head) >= 2

    def _store(self, fields: dict[str, Any]) -> int:
        """Store exactly what was given, plus an id.

        Deliberately **no** field defaults: Uptime Kuma stores the payload it was sent, so a
        fake that helpfully fills in a URL would hide a push that sent one where it should not
        have — which is precisely the bug a group with a URL is.
        """
        monitor_id = self._next_id
        self._next_id += 1
        self.monitors[monitor_id] = {"id": monitor_id, **fields}
        return monitor_id


class FakeSocket:
    """The ``socketio.Client``-shaped object the module's connector seam hands back."""

    def __init__(self, kuma: FakeKuma) -> None:
        self.kuma = kuma
        self.connected = False
        self.authenticated = False
        self._handlers: dict[str, Any] = {}

    # -- the surface python-socketio exposes that the client actually uses -----

    def on(self, event: str, handler: Any) -> None:
        self._handlers[event] = handler

    def connect(self, url: str, **kwargs: Any) -> None:
        if self.kuma.gateway_refused:
            raise ConnectionError("403 Forbidden")
        if self.kuma.unreachable:
            raise ConnectionError("Connection refused")
        self.connected = True
        self.url = url
        self.socketio_path = kwargs.get("socketio_path")
        self.headers = kwargs.get("headers") or {}
        if not self.kuma.silent:
            self._emit_info(authenticated=False)

    def disconnect(self) -> None:
        self.connected = False

    def transport(self) -> str:
        return "websocket"

    def call(self, event: str, data: Any = None, timeout: float | None = None) -> Any:
        handler = getattr(self, f"_on_{event}", None)
        if handler is None:
            return {"ok": False, "msg": "unknownEvent", "msgi18n": True}
        return handler(data)

    # -- events ----------------------------------------------------------------

    def _push(self, event: str, payload: Any) -> None:
        """Deliver a pushed event to whatever handler the client registered for it."""
        handler = self._handlers.get(event)
        if handler is not None:
            handler(payload)

    def _monitor_list(self) -> dict[str, Any]:
        """Keyed by id **as a string**, exactly as the wire delivers it."""
        return {str(k): dict(v) for k, v in self.kuma.monitors.items()}

    def _push_lists(self) -> None:
        """What a real server sends unprompted the moment a socket authenticates.

        Both lists arrive here and `notificationList` arrives *only* here — no event re-requests
        it. A client that waits for a push it never provoked has to tolerate that, so the fake
        has to reproduce it.
        """
        self._push("monitorList", self._monitor_list())
        self._push("notificationList", list(self.kuma.notifications))

    def _emit_info(self, *, authenticated: bool) -> None:
        handler = self._handlers.get("info")
        if handler is None:
            return
        payload: dict[str, Any] = {"primaryBaseURL": None, "serverTimezone": "Europe/Amsterdam"}
        if authenticated:
            # Only after login, exactly as 2.5.0 does.
            payload |= {"version": self.kuma.version, "dbType": "sqlite", "isContainer": True}
        handler(payload)

    def _on_needSetup(self, _data: Any) -> bool:
        return not self.kuma.monitors and False

    def _on_login(self, data: Any) -> dict[str, Any]:
        if self.kuma.rate_limited:
            # No `msgi18n`: the one refusal 2.x leaves as English prose.
            return {"ok": False, "msg": RATE_LIMIT_MSG}
        if data.get("username") != self.kuma.username or data.get("password") != self.kuma.password:
            return {"ok": False, "msg": "Incorrect username or password.", "msgi18n": False}
        if self.kuma.totp and not data.get("token"):
            return {"tokenRequired": True}
        if self.kuma.totp and data.get("token") != self.kuma.totp:
            return {"ok": True}  # 2.x answers neither a token nor `tokenRequired`
        self.authenticated = True
        self._emit_info(authenticated=True)
        self._push_lists()
        return {"ok": True, "token": self.kuma.token}

    def _on_loginByToken(self, token: Any) -> dict[str, Any]:
        if self.kuma.token_revoked or token != self.kuma.token:
            return {"ok": False, "msg": REAUTH_MSG, "msgi18n": True}
        self.authenticated = True
        self._emit_info(authenticated=True)
        self._push_lists()
        return {"ok": True}

    def _require_auth(self) -> dict[str, Any] | None:
        return None if self.authenticated else {"ok": False, "msg": REAUTH_MSG, "msgi18n": True}

    def _on_getMonitorList(self, _data: Any) -> Any:
        """Ack that the request was accepted; **push** the answer.

        This is the whole shape of the bug that made the module report a live instance holding
        34 monitors as empty. The ack really is a bare ``{"ok": True}``.
        """
        if (refusal := self._require_auth()) is not None:
            return refusal
        self._push("monitorList", self._monitor_list())
        return {"ok": True}

    def _on_getMonitor(self, monitor_id: Any) -> Any:
        if (refusal := self._require_auth()) is not None:
            return refusal
        monitor = self.kuma.monitors.get(int(monitor_id))
        if monitor is None:
            return {"ok": False, "msg": "monitorNotFound", "msgi18n": True}
        return {"ok": True, "monitor": dict(monitor)}

    def _on_add(self, payload: Any) -> Any:
        if (refusal := self._require_auth()) is not None:
            return refusal
        if self.kuma.is_v2() and payload.get("conditions") is None:
            # Faithful to 2.x: a NOT NULL column with no default, answered as a raw constraint
            # violation rather than a friendly message.
            return {
                "ok": False,
                "msg": "SQLITE_CONSTRAINT: NOT NULL constraint failed: monitor.conditions",
            }
        if not self.kuma.is_v2() and "conditions" in payload:
            # 1.x has no such column, and `add` imports the payload onto the row wholesale — so
            # the key a 2.x demands is, one major version down, an unknown column against the
            # tenant's own database. The two refusals are what make the version gate testable.
            return {
                "ok": False,
                "msg": "SQLITE_ERROR: table monitor has no column named conditions",
            }
        monitor_id = self.kuma._store(dict(payload))
        # `monitorID`, not 1.x's `monitorId`.
        return {"ok": True, "monitorID": monitor_id, "msg": "successAdded", "msgi18n": True}

    def _on_editMonitor(self, payload: Any) -> Any:
        if (refusal := self._require_auth()) is not None:
            return refusal
        monitor_id = int(payload.get("id"))
        if monitor_id not in self.kuma.monitors:
            return {"ok": False, "msg": "monitorNotFound", "msgi18n": True}
        # Kuma replaces the row from the payload — which is exactly why a blind write loses the
        # hundred keys the caller never modelled, and why the fake must not merge here.
        self.kuma.monitors[monitor_id] = dict(payload)
        return {"ok": True, "msg": "successEdited", "msgi18n": True}

    def _on_pauseMonitor(self, monitor_id: Any) -> Any:
        return self._set_active(monitor_id, False)

    def _on_resumeMonitor(self, monitor_id: Any) -> Any:
        return self._set_active(monitor_id, True)

    def _set_active(self, monitor_id: Any, active: bool) -> Any:
        if (refusal := self._require_auth()) is not None:
            return refusal
        monitor = self.kuma.monitors.get(int(monitor_id))
        if monitor is None:
            return {"ok": False, "msg": "monitorNotFound", "msgi18n": True}
        monitor["active"] = active
        return {"ok": True}

    def _on_deleteMonitor(self, monitor_id: Any) -> Any:
        if (refusal := self._require_auth()) is not None:
            return refusal
        self.kuma.monitors.pop(int(monitor_id), None)
        return {"ok": True, "msg": "successDeleted", "msgi18n": True}

    def _on_getTags(self, _data: Any) -> Any:
        return self._require_auth() or {"ok": True, "tags": list(self.kuma.tags)}

    def _on_getSettings(self, _data: Any) -> Any:
        """The instance's own settings under ``data`` — and **never** the notification channels.

        Reading them from here is what made `list_notifications` answer `[]` on an instance with
        Slack and e-mail configured. They only ever arrive as the login-time push.
        """
        return self._require_auth() or {"ok": True, "data": {"checkUpdate": False}}
