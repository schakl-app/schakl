"""Pure matching rules for a mailbox feed — no I/O, no vendor, so they are unit-testable once.

The pipeline decides, for one fetched message: who is on it, whether it is CRM-relevant (matched
to a known contact outside the agency, not colleague-to-colleague chatter), which records it maps
to, and whose mailbox it belongs to. Gmail hands the feed raw ``From``/``To``/``Cc`` header
strings; Outlook hands it structured recipients. Both end up as the same participant list, and
everything after that point is identical — which is why it lives here rather than twice.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from email.utils import getaddresses
from html import unescape

_HEADER_ROLES = (("From", "from"), ("To", "to"), ("Cc", "cc"))


def parse_participants(headers: dict[str, str]) -> list[dict[str, str]]:
    """``[{email, name, role}]`` from the From/To/Cc headers, addresses lowercased."""
    participants: list[dict[str, str]] = []
    for header_name, role in _HEADER_ROLES:
        raw = headers.get(header_name)
        if not raw:
            continue
        for name, address in getaddresses([raw]):
            if not address or "@" not in address:
                continue
            participants.append(
                {"email": address.lower(), "name": name or None, "role": role}  # type: ignore[dict-item]
            )
    return participants


def participants_from_addresses(
    *,
    sender: tuple[str | None, str | None] | None,
    to: Iterable[tuple[str | None, str | None]],
    cc: Iterable[tuple[str | None, str | None]],
) -> list[dict[str, str]]:
    """The same participant list from **structured** ``(name, address)`` pairs.

    A provider that already parsed its headers (Graph's ``emailAddress`` objects) must not be
    re-joined into a header string only to be split again — a display name containing a comma
    would come apart. Same order, same lowercasing, same shape as :func:`parse_participants`.
    """
    participants: list[dict[str, str]] = []
    for role, pairs in (("from", [sender] if sender else []), ("to", to), ("cc", cc)):
        for pair in pairs:
            name, address = pair
            if not address or "@" not in address:
                continue
            participants.append(
                {"email": address.lower(), "name": name or None, "role": role}  # type: ignore[dict-item]
            )
    return participants


def sender_of(participants: list[dict[str, str]]) -> str | None:
    """The ``From`` address, or ``None`` on a message that somehow has no sender."""
    for participant in participants:
        if participant["role"] == "from":
            return participant["email"]
    return None


def intended_owner(participants: list[dict[str, str]], ours: AbstractSet[str]) -> str | None:
    """Whose email this is, read from the headers — never from which mailbox fetched it.

    The feed logs one row per email (the RFC-822 dedup), so when two colleagues both hold a copy
    the owner used to be decided by *poll order*: an ``info@`` address Bcc'd on everything polled
    first and claimed the lot, and the person who actually wrote the mail never saw it in their
    review queue — a pending row has no admin escape, so it was not merely misfiled but invisible.

    The headers say it without a race:

    - **Outgoing** — the ``From``, when the sender is one of ours. A mail someone sent is theirs
      however many colleagues were copied on it.
    - **Incoming** — the first ``To`` that is one of ours, then the first such ``Cc``. Header
      order is the addressing order, so a mail *to* Jan with the shared mailbox in Cc is Jan's.

    ``None`` when no colleague is named on any of the three headers at all — the shape of a
    Bcc-only copy, which is precisely the copy that must not claim an email it can't name.
    """
    sender = sender_of(participants)
    if sender is not None and sender in ours:
        return sender
    for role in ("to", "cc"):
        for participant in participants:
            if participant["role"] == role and participant["email"] in ours:
                return participant["email"]
    return None


def direction_of(*, sent_by_mailbox: bool, sender_internal: bool = False) -> str:
    """Which way the mail went — a property of the *message*, not of the copy we fetched.

    ``sent_by_mailbox`` is the provider's own statement (Gmail's ``SENT`` label, Outlook's Sent
    Items folder) and answers it for the sender's own copy. It does not for anyone else's: a
    colleague Bcc'd on our outgoing mail holds an ordinary inbox message, and logging that as
    *inbound* says a client wrote to us when we wrote to them. So a mail whose ``From`` is one
    of ours is outbound whichever mailbox produced the copy.
    """
    return "outbound" if sent_by_mailbox or sender_internal else "inbound"


#: Which header a contact appeared on, most central first. The sender of an inbound mail and
#: the addressee of an outbound one are what it is *about*; a Cc is who was kept informed.
_ROLE_RANK = {"from": 0, "to": 1, "cc": 2}


@dataclass
class ContactMatch:
    contact_id: uuid.UUID
    #: The contact's companies, oldest link first (deterministic tie-breaking).
    company_ids: list[uuid.UUID] = field(default_factory=list)
    #: The header this contact was found on — see ``_ROLE_RANK``.
    role: str = "to"
    #: This contact is a colleague: their address reaches one of our own mailboxes.
    is_staff: bool = False
    #: This contact holds a login the agency issued them as a **client** (#274) — so the
    #: platform has already answered "are they one of us?", and the answer was no.
    is_client_login: bool = False


def internal_only(participants: list[dict[str, str]], ours: AbstractSet[str]) -> bool:
    """Colleague-to-colleague mail is not a client touchpoint — skip it entirely.

    Read off the *addresses*, never off what matched a contact row: whether anyone outside the
    agency is on this message is a fact about the message, and it is the same answer whether or
    not the people on it happen to have records (#324).
    """
    addresses = {p["email"] for p in participants}
    return bool(addresses) and addresses <= ours


def is_internal_match(
    match: ContactMatch, internal_company_ids: frozenset[uuid.UUID] = frozenset()
) -> bool:
    """Is this matched contact one of ours rather than somebody outside the agency?

    Two ways to be ours: the address reaches a colleague (``is_staff``), or every company the
    contact is linked to is the agency's own (``internal_company_ids``, itself derived from
    staff-as-contacts — so it covers ``administratie@`` and the rest of the people on our own
    record who hold no login). A contact linked to *no* company is an outsider: an unattached
    prospect is precisely the record this feed exists to fill in.

    **A client login outranks both.** The company rule is an inference — "everyone filed on our
    own record is us" — and a portal invitation is a *statement*: the agency issued this person a
    client login, which is what ``external_user_ids`` means by not-a-colleague (#274). Where the
    two disagree the statement wins, or a client the agency happens to keep on its own company has
    their whole correspondence dropped while the portal they were invited to sits there working.

    One definition, two readers — the ingest gate (:func:`has_external_match`) and the mapping
    ranking (:func:`resolve_mappings`). They were two copies of it, and only one was ever written
    down (#324).
    """
    if match.is_client_login:
        return False
    return match.is_staff or (
        bool(match.company_ids)
        and all(company_id in internal_company_ids for company_id in match.company_ids)
    )


def has_external_match(
    matches: list[ContactMatch], internal_company_ids: frozenset[uuid.UUID] = frozenset()
) -> bool:
    """Does this message name a known contact who is **not** one of ours? (#324)

    The ingest gate's question. "Matched a contact" was never it: an agency's own staff are
    contacts too, so a newsletter addressed to one colleague matched *that colleague*, opened
    the gate, and landed in their review queue as a pending contactmoment on the agency's own
    company — every supplier invoice, cold outreach and GitHub notification with it.
    """
    return any(not is_internal_match(match, internal_company_ids) for match in matches)


def _rank(match: ContactMatch, internal_company_ids: frozenset[uuid.UUID]) -> tuple[int, int]:
    """Sort key: outsiders before insiders, then by how central the header is."""
    return (
        int(is_internal_match(match, internal_company_ids)),
        _ROLE_RANK.get(match.role, len(_ROLE_RANK)),
    )


def resolve_mappings(
    matches: list[ContactMatch],
    *,
    internal_company_ids: frozenset[uuid.UUID] = frozenset(),
) -> dict[str, uuid.UUID | None]:
    """Contact + company for the interaction row.

    Every logged email lands on *some* client timeline — reachable and remappable, rather than
    floating unmapped where no panel would ever show it — so this always answers when anything
    matched. *Which* client is a ranking, not a first-come (#305):

    1. **The agency is not the client.** Insiders rank last: a staff address, and a contact
       whose companies are *all* ours (``internal_company_ids``).
    2. **A Cc is not what the mail is about.** Among the rest, the ``From`` of an inbound mail
       and the ``To`` of an outbound one outrank whoever was kept informed.
    3. **Then oldest link first**, as before — the sort is stable, so the caller's ordering is
       the tie-break and the answer stays deterministic.

    Ranked, never filtered: internal-only mail (the org's *log internal* switch) still maps to
    the agency's own company, because there is nothing else it could mean.
    """
    if not matches:
        return {}
    ranked = sorted(matches, key=lambda match: _rank(match, internal_company_ids))
    contact_id = ranked[0].contact_id if len(ranked) == 1 else None
    all_companies: list[uuid.UUID] = []
    for match in ranked:
        for company_id in match.company_ids:
            if company_id not in all_companies:
                all_companies.append(company_id)
    # One contact may sit on both sides (a colleague listed on a client's company too), so the
    # company list gets the same treatment as the contacts that produced it.
    all_companies.sort(key=lambda company_id: company_id in internal_company_ids)
    company_id = all_companies[0] if all_companies else None
    if contact_id is None and company_id is not None:
        # Several matched contacts: attribute to the best-ranked one on the chosen company.
        for match in ranked:
            if company_id in match.company_ids:
                contact_id = match.contact_id
                break
    return {"contact_id": contact_id, "company_id": company_id}


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")
#: Zero-width joiners, BOMs, soft hyphens, bidi marks — a marketing preheader's invisible
#: padding, which a provider happily includes in the preview it hands us.
_INVISIBLE_RE = re.compile("[\u00ad\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff]")


def clean_snippet(raw: str | None) -> str | None:
    """A provider's preview line, made readable (#263).

    Gmail's arrives **HTML-escaped** (``&#39;``, ``&amp;``, ``&nbsp;``) and padded with the
    message's invisible preheader, so stored raw it reads as escape codes in every list row —
    and matches nothing when someone searches the words they actually saw. Decoded and
    single-spaced once here, at the seam, not in each of the surfaces that show it.
    """
    if not raw:
        return None
    return " ".join(_INVISIBLE_RE.sub("", unescape(raw)).split()) or None


def html_to_text(html: str | None) -> str | None:
    """A body's plain-text reading of its HTML part — what search reads when there is no
    ``text/plain`` alternative to prefer."""
    if html is None:
        return None
    text = unescape(_TAG_RE.sub(" ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html)))
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_RE.sub("\n\n", text).strip() or None


def search_token(raw: str) -> str:
    """One search field's value with everything a mail search engine reads as syntax removed —
    the injection boundary of a named-field search (#372)."""
    return re.sub(r'[":()\s]+', " ", raw).strip()[:200]
