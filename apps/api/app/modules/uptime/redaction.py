"""Stripping Kuma's secrets out of the mirror, without going blind to them (docs/UPTIME.md §4).

A monitor returned by ``getMonitor`` carries its credentials **in the clear** — verified against
a live 2.5.0, which handed back the canary password this module wrote. Storing that payload
would make ``uptime_monitors`` a credential store nobody declared, readable by a detail
endpoint, and would make the client's "a credential never reaches a response" rule false the
day somebody adds a field.

Storing a bare ``True`` instead is safe and needlessly lossy: drift detection then cannot see a
password change at all. So each secret becomes ``{"set": bool, "fp": <hmac>}`` — presence stays
answerable, **"somebody changed this at Kuma" stays answerable**, and the value is unrecoverable
from the row.

The salt is per instance and random, so a fingerprint only compares inside the instance it was
taken from and an exported database is not one dictionary against every tenant at once.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

#: Every key a live 2.5.0 returned that carries a credential. Taken from the round-trip in
#: docs/UPTIME.md §2 rather than from the docs, because the docs do not enumerate them and the
#: set grows with each monitor type Kuma adds.
#:
#: ``tlsCert`` and ``tlsCa`` are deliberately **not** here: a certificate and a CA bundle are
#: public halves, and redacting them would hide a real drift (somebody repointed a monitor at a
#: different CA) to protect nothing.
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "basic_auth_pass",
        "databaseConnectionString",
        "mqttPassword",
        "oauth_client_secret",
        "rabbitmqPassword",
        "radiusPassword",
        "radiusSecret",
        "tlsKey",
    }
)

#: Kuma's own push-monitor token. Not a tenant credential in the same sense — it is the URL a
#: push monitor is pinged on — but it is a bearer secret and it is not ours to mirror.
PUSH_TOKEN_FIELD = "pushToken"


def _fingerprint(salt: str, field: str, value: str) -> str:
    """HMAC of one secret, domain-separated by its field name.

    The field name is in the message so that the same password on ``basic_auth_pass`` and
    ``radiusPassword`` does not produce one value that says they are the same secret — which is
    true, uninteresting, and more than the mirror needs to know.
    """
    mac = hmac.new(salt.encode("utf-8"), f"{field}\x00{value}".encode(), hashlib.sha256)
    return mac.hexdigest()


def redact_monitor(monitor: dict[str, Any], *, salt: str) -> dict[str, Any]:
    """A monitor payload with every secret replaced by ``{"set", "fp"}``.

    Returns a new dict; the caller's copy is untouched, because the *unredacted* payload is
    exactly what a write path must round-trip back to Kuma (§2, finding 7 — 119 keys returned
    against 16 sent) and destroying it here would be the blind-write bug one layer down.
    """
    out = dict(monitor)
    for field in SECRET_FIELDS | {PUSH_TOKEN_FIELD}:
        if field not in out:
            continue
        value = out[field]
        if value in (None, ""):
            out[field] = {"set": False, "fp": None}
        else:
            out[field] = {"set": True, "fp": _fingerprint(salt, field, str(value))}
    return out


def secret_drift(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, ...]:
    """Which secret fields changed between two **redacted** snapshots.

    Compares fingerprints, so it answers "the password at Kuma is not the one we last saw"
    without either snapshot holding a password. A field that appears or disappears counts as
    changed; a field absent from both does not.
    """
    changed: list[str] = []
    for field in sorted(SECRET_FIELDS | {PUSH_TOKEN_FIELD}):
        a, b = before.get(field), after.get(field)
        if a == b:
            continue
        # Only compare shapes this module wrote. A raw value on either side means somebody
        # stored an unredacted snapshot, which is a bug worth failing loudly on later — not a
        # drift to report to a tenant.
        if not isinstance(a, (dict, type(None))) or not isinstance(b, (dict, type(None))):
            raise ValueError(f"secret_drift compared an unredacted {field!r}")
        changed.append(field)
    return tuple(changed)
