"""Who counts as *us* — resolved once per poll, across every connected mailbox provider.

``Internals`` answers one question three ways: "is this person the agency, or somebody outside
it?" — asked to skip colleague-only chatter, to decide whether a message is CRM-relevant at all
(#324), and to rank the mapping (#305). It also answers *which* colleague, and whether their own
mailbox polls — the question behind "stand aside, the owner's mailbox logs it".

**That last answer has to be composed across providers.** A colleague may read their mail in
Outlook while the shared ``info@`` box is on Gmail; a copy the Gmail mailbox holds of a mail
addressed to that colleague must defer to the Outlook mailbox, and the reverse. Each integration
therefore registers a :class:`MailboxProvider` here and reads the union back, exactly as
``app/core/busy.py`` composes a calendar out of three modules' thirds. An integration that is
disabled registers nothing, and its mailboxes are simply absent — which reads as "that colleague
does not poll", the right answer for a mailbox nothing is reading.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mailbox.intake import load_intake_addresses
from app.core.mailbox.matching import ContactMatch
from app.core.portal import external_user_ids

logger = logging.getLogger("schakl.mailbox")


@dataclass(frozen=True)
class Mailbox:
    """One connected mailbox as the composition needs it: the address a grant was made with, the
    colleague it belongs to, and whether their own feed is actually polling it."""

    email: str
    user_id: uuid.UUID
    #: Active, opted in, holding the mail scope. Deferring to a mailbox that will never poll
    #: would lose the email outright.
    syncing: bool


#: ``(session, org_id)`` → this provider's connected mailboxes for the org. Called with the RLS
#: GUC bound; a provider reads its own connection table and nothing else.
MailboxProvider = Callable[[AsyncSession, uuid.UUID], Awaitable[list[Mailbox]]]

_providers: dict[str, MailboxProvider] = {}


def register_mailbox_provider(key: str, provider: MailboxProvider) -> None:
    """Called once by each mailbox integration at import time."""
    _providers[key] = provider


def registered_mailbox_providers() -> list[str]:
    return sorted(_providers)


@dataclass(frozen=True)
class Internals:
    """The staff addresses, the companies that are the agency's own, and who polls what."""

    #: Staff *login* addresses (``users.email``). Narrow on purpose: it is what derives
    #: ``company_ids``, and a colleague who connected a private mailbox must never make
    #: whichever company that address is a contact of read as the agency's own.
    member_emails: frozenset[str]
    #: Companies that are the agency itself rather than one of its clients.
    company_ids: frozenset[uuid.UUID]
    #: Every address that reaches a colleague → their user id. Wider than ``member_emails`` on
    #: purpose: it also carries the address each mailbox grant was made with, because what has
    #: to be found here is a *mailbox*, and someone whose ``users.email`` differs from their
    #: Workspace or Microsoft 365 address would otherwise resolve to nobody.
    owner_by_email: dict[str, uuid.UUID] = field(default_factory=dict)
    #: Users whose own mailbox is genuinely being polled, by **any** provider.
    syncing_user_ids: frozenset[uuid.UUID] = frozenset()
    #: Logins this org issued to somebody **outside** it (#274) — the answer the rest of this
    #: dataclass is built by removing. Kept because it is also a fact about a *contact*: a client
    #: the agency happens to file on its own company is still a client.
    external_user_ids: frozenset[uuid.UUID] = frozenset()
    external_emails: frozenset[str] = frozenset()
    #: The addresses the org has designated as intake (``taak@bureau.nl`` → ``"tasks"``),
    #: composed from every registered intake (:mod:`app.core.mailbox.intake`). Loaded here so
    #: the feeds ask the question with what they already hold, once per poll.
    intake_addresses: dict[str, str] = field(default_factory=dict)

    @property
    def ours(self) -> frozenset[str]:
        """Every address that is one of ours — the keys of ``owner_by_email``.

        One set answers "is this person outside the agency?" wherever it is asked: whether the
        message is colleague-to-colleague chatter and whether a matched contact row is a
        colleague's. Two sets is how a mail to somebody's alias came out *external* on one
        question and *internal* on the other (#324); the alias reaches the same person either way.
        """
        return frozenset(self.owner_by_email)


async def load_internals(session: AsyncSession, org_id: uuid.UUID) -> Internals:
    """The staff addresses, and the companies they are the contacts of.

    A client login is an ordinary membership — so a naive all-memberships set makes every
    invited client look like a colleague, and ``internal_only`` then silently drops their entire
    correspondence. Client logins are excluded through the core seam (both halves of #274: the
    contact link *and* the address), and keep matching as *contacts*, which is what they are.

    The company half is **derived, never configured**: an agency that keeps its own company in
    its own list has staff on it as contacts, and no other company does. So "a company whose
    contact is a colleague" identifies it without asking anyone to set a flag they would forget.
    Nothing is hidden on the strength of it: it only ranks a company below a genuine client.
    """
    rows = await session.execute(
        text(
            "SELECT u.id, lower(u.email) FROM users u "
            "JOIN memberships m ON m.user_id = u.id WHERE m.org_id = :oid"
        ),
        {"oid": org_id},
    )
    pairs = [(row[0], row[1]) for row in rows]
    external = await external_user_ids(session, org_id, {uid for uid, _ in pairs})
    member_emails = frozenset(email for uid, email in pairs if uid not in external)
    external_emails = frozenset(email for uid, email in pairs if uid in external)
    owner_by_email = {email: uid for uid, email in pairs if uid not in external}
    syncing: set[uuid.UUID] = set()
    for key, provider in sorted(_providers.items()):
        try:
            mailboxes = await provider(session, org_id)
        except Exception:  # noqa: BLE001 — one provider's fault must not blind the other feed
            logger.warning("mailbox provider %s failed", key, exc_info=True)
            continue
        for mailbox in mailboxes:
            if mailbox.user_id in external:
                continue
            owner_by_email.setdefault(mailbox.email.lower(), mailbox.user_id)
            if mailbox.syncing:
                syncing.add(mailbox.user_id)
    syncing_user_ids = frozenset(syncing)
    intake_addresses = await load_intake_addresses(session, org_id)
    if not member_emails:
        return Internals(
            member_emails=member_emails,
            company_ids=frozenset(),
            owner_by_email=owner_by_email,
            syncing_user_ids=syncing_user_ids,
            external_user_ids=frozenset(external),
            external_emails=external_emails,
            intake_addresses=intake_addresses,
        )
    company_rows = await session.execute(
        text(
            "SELECT DISTINCT cc.company_id FROM company_contacts cc "
            "JOIN contacts c ON c.id = cc.contact_id AND c.org_id = cc.org_id "
            "WHERE cc.org_id = :oid AND lower(c.email) = ANY(:emails)"
        ),
        {"oid": org_id, "emails": sorted(member_emails)},
    )
    return Internals(
        member_emails=member_emails,
        company_ids=frozenset(row[0] for row in company_rows),
        owner_by_email=owner_by_email,
        syncing_user_ids=syncing_user_ids,
        external_user_ids=frozenset(external),
        external_emails=external_emails,
        intake_addresses=intake_addresses,
    )


async def match_contacts(
    session: AsyncSession,
    org_id: uuid.UUID,
    participants: list[dict[str, str]],
    internals: Internals,
) -> list[ContactMatch]:
    """Participant addresses → contacts (+ their companies, oldest link first), each carrying the
    header it was found on and whether it is a colleague — which is what the ingest gate filters
    on and ``resolve_mappings`` ranks by. Bare-table lookups, never a contacts-module import
    (§6)."""
    # First occurrence wins: participants read From, To, Cc, so this is the most central header
    # each address appears on.
    roles: dict[str, str] = {}
    for participant in participants:
        roles.setdefault(participant["email"], participant["role"])
    if not roles:
        return []
    addresses = sorted(roles)
    contact_rows = await session.execute(
        text(
            "SELECT id, lower(email), user_id FROM contacts "
            "WHERE org_id = :oid AND lower(email) = ANY(:addrs) ORDER BY created_at"
        ),
        {"oid": org_id, "addrs": addresses},
    )
    found = [(row[0], row[1], row[2]) for row in contact_rows]
    if not found:
        return []
    # One query for every match's companies — per-contact would be N+1 in the poll loop.
    link_rows = await session.execute(
        text(
            "SELECT contact_id, company_id FROM company_contacts "
            "WHERE org_id = :oid AND contact_id = ANY(:cids) ORDER BY created_at"
        ),
        {"oid": org_id, "cids": [contact_id for contact_id, _, _ in found]},
    )
    companies: dict[uuid.UUID, list[uuid.UUID]] = {}
    for contact_id, company_id in link_rows:
        companies.setdefault(contact_id, []).append(company_id)
    # ``ours``, not ``member_emails``: a contact row on a colleague's alias is still a
    # colleague's, and now that the gate reads this flag, calling it an outsider is what would
    # let the newsletter back in (#324). Resolved once, not once per match.
    ours = internals.ours
    return [
        ContactMatch(
            contact_id=contact_id,
            company_ids=companies.get(contact_id, []),
            role=roles.get(email, "to"),
            is_staff=email in ours,
            # A login this org handed out to somebody outside it — which stops the company rule
            # calling a client a colleague on the strength of where they are filed (#274).
            is_client_login=email in internals.external_emails
            or (user_id is not None and user_id in internals.external_user_ids),
        )
        for contact_id, email, user_id in found
    ]


def colleagues_on(participants: list[dict[str, str]], internals: Internals) -> set[uuid.UUID]:
    """The org members a message reached, by any address that resolves to them —
    ``owner_by_email`` already excludes external logins, so a client's contact person with a
    portal account never lands in a review set."""
    return {
        internals.owner_by_email[email]
        for email in ((p.get("email") or "").lower() for p in participants)
        if email in internals.owner_by_email
    }


async def owner_name(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    """The snapshot an interaction row carries for its mailbox owner (#64)."""
    row = (
        await session.execute(
            text("SELECT full_name, email FROM users WHERE id = :uid"), {"uid": user_id}
        )
    ).first()
    if row is None:
        return None
    return row[0] or row[1]
