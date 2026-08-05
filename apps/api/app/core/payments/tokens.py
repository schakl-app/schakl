"""The callback token: how an unauthenticated provider POST names a tenant (epic #269).

A payment provider calls back with no session, no API key and no tenant hostname — it calls the
URL we handed it at payment creation. So the URL *is* the addressing, and this is the one place
that decides what it may contain.

``{org_id}.{account_id}.{secret}``, exactly the Google Calendar channel token (docs/GOOGLE.md,
``google/calendar/service.mint_channel_token``) — reused rather than reinvented because the
problem is identical and the failure mode of getting it wrong is a cross-tenant write.

Three properties, each of which a simpler design loses:

* **The org travels in the token, so nothing is ever read unscoped.** The alternative — look
  the provider's payment id up across every tenant and see whose it is — is a second unscoped
  crossing (§5 sanctions exactly one, ``core/instance/repo.py``) and it answers *before*
  authenticating, which is backwards.
* **The secret is compared, not merely present.** ``org_id`` and ``account_id`` are guessable
  in principle; the secret is not, and :func:`hmac.compare_digest` compares it in constant
  time. A wrong secret is a bare 404 — never 401 or 403, which would confirm the account.
* **It is per account, so rotating a credential rotates the URL.** The secret lives beside the
  credential it belongs to and is regenerated when that credential is replaced.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class CallbackToken:
    org_id: uuid.UUID
    account_id: uuid.UUID
    secret: str


def new_secret() -> str:
    """A fresh callback secret. URL-safe, so it survives being a path segment untouched."""
    return secrets.token_urlsafe(24)


def mint(org_id: uuid.UUID, account_id: uuid.UUID, secret: str) -> str:
    """``org.account.secret`` — how a callback maps back to a tenant."""
    return f"{org_id}.{account_id}.{secret}"


def parse(token: str) -> CallbackToken | None:
    """Read a token, or ``None`` for anything malformed.

    Deliberately total: this parses attacker-controlled input on a public endpoint, so it
    raises nothing and logs nothing. ``maxsplit=2`` keeps a secret containing a dot intact.
    """
    parts = (token or "").split(".", 2)
    if len(parts) != 3 or not parts[2]:
        return None
    try:
        return CallbackToken(uuid.UUID(parts[0]), uuid.UUID(parts[1]), parts[2])
    except ValueError:
        return None


def matches(expected: str, presented: str) -> bool:
    """Constant-time secret comparison. An empty stored secret never matches anything."""
    return bool(expected) and hmac.compare_digest(expected, presented)
