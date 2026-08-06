"""When the registration lapses — the second question ``domains`` asks about registers it may
not name.

:mod:`app.core.registrar.presence` answers *who holds this registration*; this answers *until
when*. Same shape, same reason, deliberately a separate registry: a register may know one and
not the other, and folding a date into a predicate named "presence" would make the invoiceable
rule and the renewal date share a definition they do not share.

The date matters because :attr:`~app.modules.domains.models.Domain.next_invoice_date` is
otherwise **derived** — the first yearly anniversary of ``start_date`` still ahead. That is the
expiry exactly when ``start_date`` is the real registration date, and wrong by however much it
is off when it is not: a portfolio onboarded in one afternoon has every domain anchored to that
afternoon, and every renewal invoice then goes out on the wrong day. A register that has
actually answered knows the real date; nothing else in the system does.

Two rules carried over from #298, and both are what make this safe:

* **A credential is not an authority.** A register speaks here only through a row it *stored*,
  which only a sync writes — so a connected-but-never-synced account contributes nothing and an
  undecided domain keeps the date it has. There is no separate ``authority`` clause because a
  non-NULL expiry already is one: it cannot exist without a sync having produced it.
* **Observed is not decided** (CLAUDE.md §10). This module hands back what the register
  *observed*; it never writes. Whether a stored ``next_invoice_date`` gives way to it is
  ``domains``' decision, made once in :mod:`app.modules.domains.service` — which is why nothing
  here is a setter.

Every clause is correlated to a ``domains`` row that is **passed, never imported**, so it
composes into a list read, a form's default and a cron's ``WHERE`` without costing a query per
domain (docs/PERFORMANCE.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, func


@dataclass(frozen=True)
class RegisterExpiry:
    """One module's answer to "when does this domain's registration expire?"."""

    #: Stable slug, matching the owning module (``"oxxa"``, ``"cloudflare"``) — the same key its
    #: :class:`~app.core.registrar.presence.RegisterPresence` uses, so a screen naming the
    #: register that decided the date names it the same way twice.
    key: str
    #: ``(org_id, domain) -> date scalar subquery``, NULL where this register does not hold the
    #: domain or has never read an expiry for it. ``domain`` is the correlated ``domains`` model,
    #: carrying ``.id`` and ``.name`` because a register row matches on either — linked by a
    #: sync, or by name for a domain record typed since the last one.
    expires_on: Callable[[uuid.UUID, Any], ColumnElement[date | None]]


#: Populated at module import time, the same self-registration ``ModuleDescriptor`` uses.
_EXPIRIES: dict[str, RegisterExpiry] = {}


def register_expiry(source: RegisterExpiry) -> None:
    """Register a module's expiry source. Re-registering the same key with a different object is
    a programming error, not a silent replacement (``register_presence``'s rule)."""
    existing = _EXPIRIES.get(source.key)
    if existing is not None and existing is not source:
        raise ValueError(f"register expiry {source.key!r} is already registered")
    _EXPIRIES[source.key] = source


def register_expiries() -> tuple[RegisterExpiry, ...]:
    """Every registered source, in key order. Empty when no register module is enabled — and an
    empty tuple is a real answer, not a missing one: nothing can be derived, so nothing is."""
    return tuple(_EXPIRIES[key] for key in sorted(_EXPIRIES))


def register_expiry_expression(org_id: uuid.UUID, domain: Any) -> ColumnElement[date | None] | None:
    """Every source ``COALESCE``d into one date, or ``None`` when no register is enabled.

    ``None`` rather than a ``NULL`` literal on purpose: "no register can answer" and "the
    registers answered nothing" are the same *value* and different *situations*, and the caller
    that wants to skip the read entirely can only tell them apart here.

    Order is the key order :func:`register_expiries` fixes, so two registers holding the same
    domain resolve deterministically instead of by import order. That is a rare, already-wrong
    state — one domain, two registrars — and this file's job is to be predictable in it, not to
    adjudicate it.
    """
    sources = register_expiries()
    if not sources:
        return None
    parts = [source.expires_on(org_id, domain) for source in sources]
    return parts[0] if len(parts) == 1 else func.coalesce(*parts)
