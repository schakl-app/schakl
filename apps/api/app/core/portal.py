"""Portal seams (issue #193) — what core knows about client logins, and nothing more.

A portal login is an ordinary membership whose user is linked to a **subject**: a row in some
module that names a person outside the agency. Today the only subject is a *contact*, a fact
owned by the contacts module. Two different callers need that fact and neither may import
contacts' internals (CLAUDE.md §6), so both cross here:

* :func:`portal_user_ids` — "which of these users are portal logins?", i.e. contact-linked.
  The narrow question, and the right one only where the *link* is the subject (the member list
  hides a contact-managed login and keeps a directly-invited client).
* :func:`external_user_ids` — "which of these users are **not colleagues**?" That is the
  question almost every caller actually has, and #274 already answered it: an external login is
  a contact link **or** the seeded ``client`` role, because a client invited straight from
  Instellingen → Gebruikers carries no contact link at all. Asking the narrow question there
  silently counts a client as staff.
* :class:`PortalSubjectProvider` — the read/write handle the **portal module** works through.
  Inviting a client, disabling the login and signing in as them are all the portal module's
  business, but the row that says *who the client is* belongs to whoever owns the subject.

The second seam is what lets ``portal`` be a module at all rather than a wing of ``contacts``.
It is deliberately the smallest surface that covers the flows: load a subject (through the
owner's own repository, so the company horizon applies), find the subject behind a user, and
attach a freshly created login to it. Everything else — the account, the membership, the
invite mail, the impersonation grant, the activity trail — is core or the portal module's own.

No provider registered (contacts disabled) = no portal subjects, and the portal module's
routes answer 404. That is the honest answer: without contacts there is nobody to invite.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:  # only for the annotations — importing tenancy here would cycle.
    from app.core.tenancy import RequestContext

PortalUserResolver = Callable[
    [AsyncSession, uuid.UUID, set[uuid.UUID]], Awaitable[set[uuid.UUID]]
]

_resolver: PortalUserResolver | None = None


def register_portal_user_resolver(resolver: PortalUserResolver) -> None:
    """Called once by the contacts module's package ``__init__``."""
    global _resolver
    _resolver = resolver


async def portal_user_ids(
    session: AsyncSession, org_id: uuid.UUID, candidates: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of ``candidates`` are portal logins in this org. Empty when none (or when the
    contacts module is disabled — then no portal logins can exist either)."""
    if _resolver is None or not candidates:
        return set()
    return await _resolver(session, org_id, candidates)


async def external_user_ids(
    session: AsyncSession, org_id: uuid.UUID, candidates: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of ``candidates`` are logins from **outside** the agency (#274).

    The same fact ``ctx.is_portal`` resolves per request, asked of a *set* of users: a contact
    link (:func:`portal_user_ids`) **or** the seeded ``client`` role. Both halves are needed
    and neither implies the other — a contact invited through their own portal section holds
    the link, a client invited from Instellingen → Gebruikers holds only the role, and CLAUDE.md
    §15 says so in as many words: "gating one on the contact link alone silently exempts a
    directly-invited client".

    Two callers answer this question inside a statement they were already running — the
    notification fan-out (flat cost in the recipient count) and the cloud domain-health
    recipients — and keep their inline copy on purpose. Every other caller should be here: the
    gmail feed asked the narrow question and read a client's whole correspondence as
    colleague-to-colleague chatter, which drops it (#324's gate) with no trace at all.
    """
    if not candidates:
        return set()
    from sqlalchemy import select

    from app.core.models import Membership
    from app.core.permissions.catalog import ROLE_CLIENT
    from app.core.permissions.models import MembershipRole, Role

    external = await portal_user_ids(session, org_id, candidates)
    remaining = candidates - external
    if not remaining:
        return external
    rows = await session.execute(
        select(Membership.user_id)
        .join(MembershipRole, MembershipRole.membership_id == Membership.id)
        .join(Role, Role.id == MembershipRole.role_id)
        .where(
            Membership.org_id == org_id,
            Membership.user_id.in_(remaining),
            MembershipRole.org_id == org_id,
            Role.key == ROLE_CLIENT,
        )
    )
    return external | set(rows.scalars())


# --------------------------------------------------------------------------- #
# Portal subjects — the person a client login belongs to
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PortalSubject:
    """One row that may carry a client login, in the only vocabulary core needs.

    ``entity_type`` is the same string ``AuditableMixin`` registers (``"contact"``), so the
    portal module records its trail against the subject's own entity without ever naming it.
    """

    entity_type: str
    id: uuid.UUID
    #: The address an invite goes to. ``None`` is a real state — a contact with no e-mail
    #: cannot be invited, and the portal module reports that rather than guessing.
    email: str | None
    display_name: str | None
    #: The login already attached, if any. ``None`` = no portal login for this subject yet.
    user_id: uuid.UUID | None


@dataclass(frozen=True)
class PortalSubjectClient:
    """A client the subject belongs to, in the only two fields a register prints.

    Core learns a company's *name* here and nothing else about it: the row is read by whoever
    owns the subject, which is the module that already knows how its people attach to clients
    (``company_contacts`` for a contact, something else for the next kind).
    """

    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class PortalSubjectListing:
    """One subject that **already carries a login**, and the clients it belongs to.

    The register's row (#406). Deliberately not :class:`PortalSubject` with a field bolted on:
    the clients are only ever resolved for the listing — every other caller works one subject
    at a time and would pay for a join it does not read.
    """

    subject: PortalSubject
    clients: tuple[PortalSubjectClient, ...] = ()


class PortalSubjectProvider(Protocol):
    """What a module implements to offer its rows as portal subjects.

    Registered once, at import time, by the owning module's package ``__init__`` — the same
    shape as the company-scope resolver (``app/core/scope.py``) and the automation action
    specs: modules meet at a registry, never at an import.
    """

    entity_type: str

    async def load(
        self, ctx: RequestContext, subject_id: uuid.UUID
    ) -> PortalSubject | None:
        """The subject, read **through the owner's own repository** — so a caller restricted to
        a company group can only reach the clients it may see, and anything else is ``None``
        (the portal module turns that into the same 404 every other surface gives)."""
        ...

    async def list_logins(self, ctx: RequestContext) -> list[PortalSubjectListing]:
        """Every subject of this kind that already carries a login — the register (#406).

        Three rules, and the first is why this is a **protocol method** rather than a query the
        portal module writes for itself: enumerating subjects means reading the owner's table,
        and §6 says a module names no other module's internals. The same reason
        ``app/core/directory.py`` exists.

        The second is that it reads **through the owner's own repository**, exactly as
        :meth:`load` does — so a staff member restricted to a company group sees only the logins
        of clients inside it, and the count above the list, being ``len()`` of these rows, is
        narrowed by construction rather than by a second statement that could disagree.

        The third is that it is **batched**: one statement for the subjects and one per lookup
        they share, never a state call per row (docs/PERFORMANCE.md). A subject with no login is
        not a row here — ``none`` is the absence of a login, not a kind of one.
        """
        ...

    async def for_user(
        self, ctx: RequestContext, user_id: uuid.UUID
    ) -> PortalSubject | None:
        """The subject behind a login, resolved **horizon-blind on purpose**.

        Its one caller is a portal session ending its own impersonation, where the row *is* the
        caller: a client attached to no company has an empty horizon (#193) and would not find
        itself, which would silently drop the stop from the audit trail.
        """
        ...

    async def attach(
        self, ctx: RequestContext, subject_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Link a newly created login to the subject. Called inside the portal module's own
        transaction, after the user and membership exist."""
        ...


_subject_providers: dict[str, PortalSubjectProvider] = {}


def register_portal_subject_provider(provider: PortalSubjectProvider) -> None:
    _subject_providers[provider.entity_type] = provider


def portal_subject_provider(entity_type: str) -> PortalSubjectProvider | None:
    """The provider for an ``entity_type``, or ``None`` when no enabled module offers it —
    which is also the answer for an entity type that has nothing to do with portals."""
    return _subject_providers.get(entity_type)


def portal_subject_types() -> list[str]:
    """Every entity type that can carry a client login, for the surfaces that have to offer a
    choice. One today (``contact``); the seam is why a second costs no core change."""
    return sorted(_subject_providers)
