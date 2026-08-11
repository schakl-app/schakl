"""Resolving what a monitor's settings actually are (docs/UPTIME.md §8).

**Three layers, and they must not fuse** — the argument `docs/REPORTING.md` makes about prompts,
in a smaller key:

1. **Product invariants are code.** An interval below what Uptime Kuma will accept is not a
   tenant's decision to make, and a floor compiled into a profile is a floor nobody can find.
2. **The tenant's editorial default is a row** (``uptime_monitor_profiles``). A house policy
   compiled into Python is a decision we took for them.
3. **What is true about this one monitor is that monitor's own row.**

``None`` means *inherit* and a value means *override*, which is why every overridable field on
:class:`~app.modules.uptime.models.UptimeMonitor` is nullable. An explicit ``null`` posted is how
"volg de standaard" is expressed (§18) — the same rule the marketing compare-period follows.

**And the resolution is one function**, taken by the create form, the write-back, the drift check
and the reconcile alike (#298's rule). Two copies of "what the default is" means the form writes
one thing and the drift check expects another, and every monitor in the tenant reads as drifted
forever.
"""

from __future__ import annotations

from typing import Any

#: Product invariants (layer 1). Not tenant-editable, and deliberately few: everything here is
#: something Uptime Kuma itself refuses or something that would make a monitor useless.
#:
#: The interval floor is Kuma's own (``interval`` is seconds and its UI will not go below 20).
#: A tenant who wants 10-second checks is asking for something the far end will reject, and
#: finding that out at the push is worse than being told here.
MIN_INTERVAL_SECONDS = 20
MAX_INTERVAL_SECONDS = 86_400
MIN_RETRIES = 0
MAX_RETRIES = 10

#: What a monitor gets when neither the tenant nor the monitor says otherwise. Kuma's own
#: defaults, so an agency that configures nothing still gets monitors that behave like the ones
#: they would have made by hand in Kuma's UI.
BUILT_IN_DEFAULTS: dict[str, Any] = {
    "interval_seconds": 60,
    "retries": 1,
    "retry_interval_seconds": 60,
    "resend_interval": 0,
    "timeout_seconds": 48,
    "method": "GET",
    "accepted_status_codes": ["200-299"],
    "max_redirects": 10,
    "upside_down": False,
    "expiry_notification": True,
    "ignore_tls": False,
}

#: The keys a profile is allowed to set. An allow-list, never a deny-list (§18's rule applied to
#: defaults): a field added to a monitor tomorrow is not silently profile-writable today, and
#: nothing that identifies *which* thing is watched — a URL, a hostname, a port — can ever come
#: from a shared default, because a profile that can set a URL is a profile that can point forty
#: monitors at the wrong host in one save.
PROFILE_KEYS: frozenset[str] = frozenset(BUILT_IN_DEFAULTS)


def clamp(settings: dict[str, Any]) -> dict[str, Any]:
    """Layer 1. Applied **last**, so nothing a tenant configures can slip under an invariant."""
    out = dict(settings)
    interval = out.get("interval_seconds")
    if isinstance(interval, int):
        out["interval_seconds"] = max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, interval))
    retries = out.get("retries")
    if isinstance(retries, int):
        out["retries"] = max(MIN_RETRIES, min(MAX_RETRIES, retries))
    return out


def profile_defaults(profile: Any | None) -> dict[str, Any]:
    """Layer 2, filtered to what a profile may say. Unknown keys are dropped, not trusted."""
    if profile is None:
        return {}
    raw = profile.defaults or {}
    return {k: v for k, v in raw.items() if k in PROFILE_KEYS and v is not None}


def resolve(monitor_overrides: dict[str, Any], profile: Any | None) -> dict[str, Any]:
    """The effective settings for one monitor: built-ins, then profile, then its own overrides.

    ``None`` in ``monitor_overrides`` means *inherit* and never *clear*, which is what makes an
    unticked-and-saved form fall back to the profile rather than silently pinning a null.
    """
    effective = dict(BUILT_IN_DEFAULTS)
    effective.update(profile_defaults(profile))
    effective.update({k: v for k, v in monitor_overrides.items() if v is not None})
    return clamp(effective)


def pick_profile(profiles: list[Any], monitor_type: str, explicit: Any | None) -> Any | None:
    """Which profile applies: the one named, else the tenant's default for this monitor type.

    Resolving to *nothing* is the failure `docs/REPORTING.md` already paid for once — a template
    that resolved to none silently threw four settings away. So the fallback walks outward: the
    default for this type, then the default for any type, then the oldest active profile. **The
    first profile of a type is its default**, because nobody makes one profile and means "use
    none of it".
    """
    if explicit is not None:
        return explicit
    active = [p for p in profiles if p.active]
    for candidate in (
        [p for p in active if p.is_default and p.monitor_type == monitor_type],
        [p for p in active if p.monitor_type == monitor_type],
        [p for p in active if p.is_default],
    ):
        if candidate:
            return sorted(candidate, key=lambda p: (p.position, p.created_at))[0]
    return None


#: The monitor fields drift is computed over: what schakl decided, and therefore what it is
#: entitled to have an opinion about. Everything else Kuma holds is theirs alone — a monitor's
#: heartbeat history is not something we can "disagree" with.
#:
#: Deliberately *not* including ``active``: pausing is its own permission and its own verb, and
#: a monitor paused in Kuma during an incident must not read as a configuration conflict that
#: somebody has to resolve.
DRIFT_FIELDS: tuple[str, ...] = ("name", "monitor_type", "target", "port", "interval_seconds",
                                 "retries")

#: How a monitor field maps onto the key Uptime Kuma uses for it. One table, read in both
#: directions — by the write path building a payload and by the drift check reading one back —
#: so the two can never disagree about what ``interval`` means.
KUMA_FIELDS: dict[str, str] = {
    "name": "name",
    "monitor_type": "type",
    "port": "port",
    "interval_seconds": "interval",
    "retries": "maxretries",
}

#: The Kuma key that holds "what is being watched", per monitor type. A URL for the HTTP family,
#: a hostname for the ping/port/DNS family — one concept, and the type says where it lives.
TARGET_FIELD_BY_TYPE: dict[str, str] = {
    "http": "url",
    "keyword": "url",
    "json-query": "url",
    "real-browser": "url",
    "grpc-keyword": "grpcUrl",
    "docker": "docker_container",
}
DEFAULT_TARGET_FIELD = "hostname"


def target_field(monitor_type: str) -> str:
    return TARGET_FIELD_BY_TYPE.get(monitor_type, DEFAULT_TARGET_FIELD)


def observed_value(field: str, snapshot: dict[str, Any], monitor_type: str) -> Any:
    """What Uptime Kuma currently says about one of *our* fields."""
    if field == "target":
        return snapshot.get(target_field(monitor_type))
    return snapshot.get(KUMA_FIELDS.get(field, field))


def compute_drift(monitor: Any, snapshot: dict[str, Any]) -> tuple[str, ...]:
    """Which of our fields Uptime Kuma disagrees with.

    An **adopted** monitor never drifts on its first sync: it has no intent of its own yet, so
    the observed state simply is the truth. Only a monitor whose settings schakl decided can
    disagree with the far end.
    """
    if monitor.adopted:
        return ()
    drifted: list[str] = []
    for field in DRIFT_FIELDS:
        ours = getattr(monitor, field, None)
        theirs = observed_value(field, snapshot, monitor.monitor_type)
        if ours is None or theirs is None:
            # "We never said" and "Kuma does not report it for this type" are both silence, and
            # silence is not disagreement.
            continue
        if str(ours) != str(theirs):
            drifted.append(field)
    return tuple(drifted)


def profile_defaults_input(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitise what a client posted as a profile's ``defaults``.

    An allow-list on the way *in* as well as on the way out, because ``defaults`` is JSONB and
    would otherwise happily store whatever was sent — including a ``url``, which is the one
    thing a shared default must never carry (a profile that can set a URL is a profile that can
    point forty monitors at the wrong host in one save).
    """
    if not raw:
        return {}
    return {k: v for k, v in raw.items() if k in PROFILE_KEYS and v is not None}
