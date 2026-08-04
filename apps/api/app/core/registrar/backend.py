"""The ``RegistrarProvider`` protocol and its dispatch (issue #296, epic #278 §1).

A registrar is the authority on four facts a domain record cannot know by looking at DNS: **when
it expires**, **whether it is locked against transfer**, **which nameservers are delegated at the
registry** (as opposed to what public DNS currently answers), and **who the registrant is**. This
module says what asking for those looks like, so the second registrar costs a file rather than a
refactor — the design #95 asked for and never got to build.

Three rules the shape encodes, all of them learned from the Cloudflare half of #278:

* **Everything is an observation.** A method returns what the registrar said, never a decision.
  Deciding — and storing the decision beside the observation so drift is expressible (CLAUDE.md
  §10) — is the calling module's job, in its own tables.
* **A domain is addressed as ``(sld, tld)``, not as a name.** Registrar APIs overwhelmingly split
  it (OXXA's every command takes both), and the split is *not* string surgery: only the
  registrar's own TLD list can say whether ``co.uk`` is a suffix or ``example.nl`` is. So the
  split is a provider method, not a helper in core.
* **Nameservers are a set, not a list.** Every registrar returns them in its own order and
  registries reorder them freely; comparing them ordered manufactures drift that is not there.

There is deliberately **no ``create``/``transfer``/``renew``** here. Those spend money and are
irreversible; this seam exists for the sync-and-repoint slice, and a later issue that wants to
register domains from schakl should extend it consciously rather than inherit the power by
accident.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar, Protocol, runtime_checkable


class RegistrarError(RuntimeError):
    """A registrar call failed.

    ``message`` is the **provider's own text** and is therefore untranslatable: it belongs on a
    row's ``last_error`` where a user can read it, never in the error envelope, whose ``message``
    is an i18n key (CLAUDE.md §9). ``code`` is the provider's own status token where it gave one.
    """

    def __init__(
        self, message: str, *, code: str | None = None, http_status: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class RegistrarAuthError(RegistrarError):
    """The credential was rejected. Its own class because retrying cannot help — only the tenant
    can fix it, by re-entering the credential."""


@dataclass(frozen=True)
class RegistrarContact:
    """A registry contact (registrant / admin-c / tech-c / billing-c) as the registrar holds it.

    ``ref`` is the registrar's own handle for it. It is the ``external_id`` of this seam: shared
    across every domain that points at it, which is exactly why nothing here writes one — an
    edit would silently rewrite the WHOIS of every other domain using the same handle.
    """

    ref: str
    organisation: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None

    def display_name(self) -> str:
        """Organisation if there is one, else the person. Empty string when the registrar gave
        neither — a handle with no name is a real state, not an error."""
        if self.organisation:
            return self.organisation.strip()
        parts = [p.strip() for p in (self.first_name, self.last_name) if p and p.strip()]
        return " ".join(parts)


@dataclass(frozen=True)
class RegistrarDomain:
    """One domain as the registrar last described it.

    Every field except ``sld``/``tld`` is optional on purpose: registrars answer different
    subsets per command, and a partial answer must be storable. ``None`` means *not reported*,
    which is not the same as ``False`` — a ``dnssec=None`` must never be rendered as "off".
    """

    sld: str
    tld: str
    #: Registrar handles by role: ``registrant`` / ``admin`` / ``tech`` / ``billing``.
    contacts: dict[str, str] = field(default_factory=dict)
    expires_on: date | None = None
    transfer_lock: bool | None = None
    autorenew: bool | None = None
    dnssec: bool | None = None
    #: Delegated at the **registry**. Normalised lowercase, no trailing dot. ``None`` = not
    #: reported; ``[]`` = reported as none, which is a different and alarming thing.
    nameservers: list[str] | None = None
    #: The provider's handle for the nameserver grouping, where it has one (OXXA's ``nsgroup``).
    #: Opaque to core — it exists so a caller can tell "unchanged" from "repointed".
    nameserver_ref: str | None = None
    #: Whatever the registrar calls this domain's state, stored raw. Vocabularies differ.
    status: str | None = None

    @property
    def name(self) -> str:
        return f"{self.sld}.{self.tld}"


@runtime_checkable
class RegistrarProvider(Protocol):
    """What schakl may ask a registrar. One instance per credential, cheap to construct."""

    #: Stable slug, unique across registrars (``"oxxa"``). Matches the owning module's name.
    key: ClassVar[str]

    async def verify(self) -> dict[str, Any]:
        """Prove the credential works. Returns a small provider-specific fact dict for display
        (a balance, an account name). Raises :class:`RegistrarAuthError` when rejected."""
        ...

    async def suffixes(self) -> list[str]:
        """Every TLD this credential may operate on, longest-suffix-splittable.

        The authority for turning ``klant.co.uk`` into ``("klant", "co.uk")``. A registrar that
        cannot enumerate them returns ``[]``, and the caller must then refuse to guess.
        """
        ...

    async def list_domains(self) -> list[RegistrarDomain]:
        """Every domain under this credential. One call where the API allows it — a register
        sync that costs one request per domain is a cron, not a button (docs/PERFORMANCE.md)."""
        ...

    async def get_domain(self, sld: str, tld: str) -> RegistrarDomain | None:
        """One domain, in as much detail as the registrar offers. ``None`` if it does not hold
        it — a domain the agency moved away is not an error."""
        ...

    async def get_contact(self, ref: str) -> RegistrarContact | None:
        """Resolve a contact handle. ``None`` when the handle is unknown to the registrar."""
        ...

    async def set_nameservers(self, sld: str, tld: str, nameservers: Sequence[str]) -> str:
        """Delegate ``sld.tld`` to exactly ``nameservers``. Returns the provider's reference for
        the resulting grouping (``RegistrarDomain.nameserver_ref``), so the caller can store what
        it pushed rather than re-reading to find out.

        **Must be idempotent**: called twice with the same set, the second call is a no-op that
        returns the same reference. The whole retry story of #278's orchestration rests on it.
        """
        ...


#: Registrars by key. Populated by each module at import time, the same self-registration
#: ``ModuleDescriptor`` uses — core names no registrar (CLAUDE.md §6).
_REGISTRARS: dict[str, type] = {}


def register_registrar(key: str, factory: type) -> None:
    """Register a provider class under ``key``. Re-registering the same key is a programming
    error, not a silent replacement — two registrars answering to one slug is unfixable at
    runtime."""
    existing = _REGISTRARS.get(key)
    if existing is not None and existing is not factory:
        raise ValueError(f"registrar {key!r} is already registered")
    _REGISTRARS[key] = factory


def get_registrar(key: str) -> type:
    """The provider class for ``key``. Raises :class:`LookupError` when its module is disabled —
    the caller turns that into a 409, never a 500."""
    try:
        return _REGISTRARS[key]
    except KeyError as exc:
        raise LookupError(f"no registrar registered for {key!r}") from exc


def known_registrars() -> tuple[str, ...]:
    """Registered keys, sorted. Only the *enabled* modules appear — which is what makes this
    safe to render as a picker."""
    return tuple(sorted(_REGISTRARS))


def split_suffix(name: str, suffixes: Sequence[str]) -> tuple[str, str] | None:
    """``("klant", "co.uk")`` for ``klant.co.uk`` given the registrar's own TLD list.

    Longest match wins, so a registrar carrying both ``uk`` and ``co.uk`` splits the latter.
    Returns ``None`` when no suffix matches **or** when what is left is not a single label —
    ``shop.klant.nl`` is a hostname inside a zone, not a registrable domain, and addressing the
    registrar with ``sld="shop"`` would silently operate on the wrong object.
    """
    host = name.strip().lower().rstrip(".")
    if not host or not suffixes:
        return None
    best: str | None = None
    for suffix in suffixes:
        candidate = suffix.strip().lower().strip(".")
        if not candidate or not host.endswith("." + candidate):
            continue
        if best is None or len(candidate) > len(best):
            best = candidate
    if best is None:
        return None
    label = host[: -(len(best) + 1)]
    if not label or "." in label:
        return None
    return label, best
