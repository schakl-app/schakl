"""Who holds the registration — the one question ``domains`` asks about registers it may not name.

A domain record cannot know, by itself, whether the agency is the party paying to renew it. The
registers do: OXXA's reseller list, Cloudflare Registrar's domain list. But ``domains`` is the
module those integrations attach *to*, and §6 forbids it importing either — the integration may
not even be enabled. So the question crosses at a seam, exactly as a cross-module row reference
does (:mod:`app.core.directory`): each mirroring module registers **its own SQL**, core composes
it, and neither end learns the other's tables.

Two clauses, because "is this domain in a register" is only half an answer:

* :attr:`RegisterPresence.authority` — *has this org's register actually been read?* A stored
  credential knows nothing until it has synced; a token that can edit DNS but never listed a
  registrar is not evidence about anything. Until some register answers yes, an undecided domain
  keeps billing — which is what makes this safe to ship to an instance that already invoices
  domains today: nothing changes until the agency connects a register and syncs it.
* :attr:`RegisterPresence.holds` — *does that register hold this domain?* Correlated to a
  ``domains`` row, so it composes into a list filter, a cron's ``WHERE`` and a per-row read
  without ever costing a query per domain (docs/PERFORMANCE.md).

Both are built by the owning module from its own models and handed over as callables: the module
owns the SQL, core owns only the composition. A module that is disabled never imports, never
registers, and therefore never speaks.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement


@dataclass(frozen=True)
class RegisterPresence:
    """One module's answer to "do we hold this domain's registration?"."""

    #: Stable slug, matching the owning module (``"oxxa"``, ``"cloudflare"``). Reported as the
    #: *reason* a domain resolved the way it did, so a screen can name the register.
    key: str
    #: ``(org_id) -> bool clause``. True once this org holds a register of this kind that has
    #: been read at least once. Uncorrelated to any row: Postgres evaluates it once per query.
    authority: Callable[[uuid.UUID], ColumnElement[bool]]
    #: ``(org_id, domain) -> bool clause``, where ``domain`` is the correlated ``domains`` model
    #: — **passed, never imported**, so the borrower still cannot reach into the module it is
    #: correlating against. It carries ``.id`` and ``.name`` because a register row matches on
    #: either: linked by a sync, or by name for a domain record created since the last one.
    holds: Callable[[uuid.UUID, Any], ColumnElement[bool]]


#: Populated at module import time, the same self-registration ``ModuleDescriptor`` uses.
_PRESENCES: dict[str, RegisterPresence] = {}


def register_presence(presence: RegisterPresence) -> None:
    """Register a module's presence source. Re-registering the same key with a different object
    is a programming error, not a silent replacement (``register_registrar``'s rule)."""
    existing = _PRESENCES.get(presence.key)
    if existing is not None and existing is not presence:
        raise ValueError(f"register presence {presence.key!r} is already registered")
    _PRESENCES[presence.key] = presence


def register_presences() -> tuple[RegisterPresence, ...]:
    """Every registered source, in key order. Empty when no register module is enabled — and an
    empty tuple is a real answer, not a missing one: nothing can be derived, so nothing is."""
    return tuple(_PRESENCES[key] for key in sorted(_PRESENCES))
