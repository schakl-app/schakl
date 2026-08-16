"""Matching a found monitor to the website or domain it watches (docs/UPTIME.md §9, #321).

An agency's first act with this module is adopting a Uptime Kuma that has been running for
years, and until now that produced a hundred mirrored rows attached to nothing: the website
panel — the surface this module exists to draw — was empty for precisely the monitors it was
built for, and every one of them sat at ``company_id IS NULL``, which the staff horizon reads as
"not client data, stays visible" and the portal clause reads as "not yours". Wrong on both
sides at once, and nothing on the screen said so.

Four rules shape what is here, and none of them is about Uptime Kuma.

**A pre-check normalises the way the write does** (§17). The host is taken out of whatever the
monitor's type stores — a URL for the HTTP family, a bare hostname for the ping family — and
lowercased, de-ported, de-pathed and de-dotted before anything is compared, because a match on
raw text finds nothing and then reports "niets gevonden" about a domain that is right there.

**A match is proposed, never applied.** The output is candidates; a human confirms. That is
docs/UPTIME.md §9's *"our mirror plus a set of links a human confirmed"*, and the reason is
one bad link is worse than a hundred unlinked rows: attaching a client's monitoring to another
client's record is invisible afterwards, because every row is still valid.

**More than one candidate is an answer, not a failure.** An agency that holds both ``klant.nl``
and ``shop.klant.nl`` as domain records has two defensible anchors for a monitor on
``a.shop.klant.nl``, and picking the longest suffix would be this module deciding something it
cannot know. Both are handed back and the screen asks.

**The specific anchor beats the general one.** A website is a hostname somebody has already
told us about, so an exact website-host match ends the search; a domain match is the fallback,
and it is what covers a client's mail server, VPN endpoint and NAS — hosts inside a zone we
hold that will never be websites.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, column, func, select, table

#: Two of another module's tables, referenced as bare tables (§6) — the idiom `cloudflare`
#: already uses for `domains`. A lookup is not a data path into another module.
_domains = table("domains", column("id"), column("org_id"), column("company_id"), column("name"))
_websites = table("websites", column("id"), column("org_id"), column("domain_id"), column("root"))
_hosting = table("hosting", column("id"), column("org_id"), column("company_id"))

#: What a monitor's link may point at. `hosting` is linkable by hand but never *matched*: a
#: hosting account has no hostname of its own to compare, and guessing one from the client it
#: belongs to would attach every one of that client's monitors to it.
LINKABLE = ("website", "domain", "hosting")

#: The derived states a monitor's link can be in. Derived and never stored, so a link made by
#: hand changes the answer immediately and there is no second column to keep in step.
LINKED = "linked"
MATCHED = "matched"
AMBIGUOUS = "ambiguous"
UNMATCHED = "unmatched"

#: Not one of the derived states above — a *filter* value covering all three unlinked ones. The
#: picker that offers "which monitor watches this website" asks for it, because its question is
#: "what may I still attach", and that is a different set from any one matcher outcome.
UNLINKED = "unlinked"

#: How far up a hostname to look for a domain we hold. Four levels covers
#: `status.api.klant.co.uk` and keeps the batched `IN` list bounded on an instance with a
#: thousand monitors — an unbounded read is the one docs/PERFORMANCE.md bans outright.
_MAX_PARENTS = 4


@dataclass(frozen=True)
class LinkCandidate:
    """One anchor a monitor could be attached to, with the client that anchor belongs to.

    ``company_id`` rides along because the screen wants to say *whose* it is, but it is
    deliberately **re-resolved when the link is actually applied**: a candidate is an
    observation from the last sync, and a domain that changed hands since would otherwise
    write yesterday's client onto today's monitor.
    """

    entity_type: str
    entity_id: uuid.UUID
    label: str
    company_id: uuid.UUID | None

    def as_json(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id),
            "label": self.label,
            "company_id": str(self.company_id) if self.company_id else None,
        }


def host_of(target: str | None) -> str | None:
    """The hostname a monitor watches, normalised the way a comparison needs it.

    Takes a URL or a bare host and answers the host: scheme, credentials, port, path and the
    trailing dot all removed, lowercased. An IPv6 literal keeps its brackets stripped and will
    simply never match a domain name, which is the correct outcome rather than a special case.
    """
    value = (target or "").strip().lower()
    if not value:
        return None
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0]
    if "@" in value:  # user:pass@host — credentials in a URL are rare and still legal
        value = value.rsplit("@", 1)[1]
    if value.startswith("["):
        value = value[1:].split("]", 1)[0]
    else:
        value = value.split(":", 1)[0]
    value = value.rstrip(".")
    return value or None


def lookup_hosts(hosts: set[str]) -> set[str]:
    """Every name worth asking the database about, for a batch of monitor hosts.

    A host and its parents: `vpn.klant.nl` may be matched by a domain record for `klant.nl`,
    which is how an agency's monitors on a client's mail server and VPN endpoint find their
    client at all. Single-label names (`nl`) are never asked for.
    """
    names: set[str] = set()
    for host in hosts:
        labels = host.split(".")
        for cut in range(0, min(len(labels) - 1, _MAX_PARENTS)):
            parent = ".".join(labels[cut:])
            if parent.count(".") >= 1:
                names.add(parent)
    return names


def index_query(
    org_id: uuid.UUID, scope: frozenset[uuid.UUID] | None, names: set[str]
) -> Select[Any]:
    """One statement for the whole batch: every domain in ``names`` plus its websites.

    A `LEFT JOIN` rather than two reads, because a domain with no website is still an anchor —
    and because two queries that must agree about which rows the horizon allows are two places
    to forget it. The horizon is applied to the **domain**, which is where a website's client
    lives too (`Website.__company_horizon_clause__`).
    """
    conditions = [_domains.c.org_id == org_id, func.lower(_domains.c.name).in_(names)]
    if scope is not None:
        conditions.append(_domains.c.company_id.in_(scope))
    return (
        select(
            _domains.c.id,
            _domains.c.name,
            _domains.c.company_id,
            _websites.c.id.label("website_id"),
            _websites.c.root,
        )
        .select_from(
            _domains.outerjoin(
                _websites,
                (_websites.c.domain_id == _domains.c.id)
                & (_websites.c.org_id == _domains.c.org_id),
            )
        )
        .where(*conditions)
    )


Index = dict[str, list[LinkCandidate]]


def build_index(rows: list[Any]) -> tuple[Index, Index]:
    """``(websites by host, domains by name)`` from the rows :func:`index_query` returned.

    Two indexes rather than one, because the ladder in :func:`candidates_for` is a *preference*
    and collapsing them would make a domain match compete with the website match it should
    always lose to.
    """
    websites: dict[str, list[LinkCandidate]] = {}
    domains: dict[str, list[LinkCandidate]] = {}
    for row in rows:
        apex = (row.name or "").strip().lower().rstrip(".")
        if not apex:
            continue
        existing = domains.setdefault(apex, [])
        if not any(c.entity_id == row.id for c in existing):
            existing.append(LinkCandidate("domain", row.id, apex, row.company_id))
        if row.website_id is not None:
            host = apex if row.root else f"www.{apex}"
            websites.setdefault(host, []).append(
                LinkCandidate("website", row.website_id, host, row.company_id)
            )
    return websites, domains


def candidates_for(
    host: str | None,
    websites: dict[str, list[LinkCandidate]],
    domains: dict[str, list[LinkCandidate]],
) -> list[LinkCandidate]:
    """The anchors this host could mean — most specific first, and never narrowed to one.

    The ladder stops at the first rung that answers: an exact website host is a hostname
    somebody already recorded, so a domain match underneath it would be a worse answer to the
    same question. Everything below is the zone the host sits in, and *all* the domains that
    contain it come back — two of them is an ambiguity for a person to resolve, not a tie for
    this function to break.
    """
    if not host:
        return []
    exact = websites.get(host)
    if exact:
        return list(exact)
    labels = host.split(".")
    found: list[LinkCandidate] = []
    for cut in range(0, min(len(labels) - 1, _MAX_PARENTS)):
        found.extend(domains.get(".".join(labels[cut:]), []))
    return found


def anchor_query(
    kind: str, org_id: uuid.UUID, scope: frozenset[uuid.UUID] | None, ids: list[uuid.UUID]
) -> Select[Any]:
    """``(id, company_id)`` for the anchors of one kind — the horizon written once per kind.

    This is what makes a link **safe**: the client a monitor gets is read off the anchor row the
    caller can actually see, so a member scoped to one company group cannot attach a monitor to
    another client's website by posting its id, and an anchor outside the horizon comes back as
    nothing at all (the caller's 404, §15's rule about not revealing that a row exists).

    A website's client is its *domain's* — there is no ``company_id`` on a website — which is
    why this is a join rather than three copies of one column match (#285's first failure mode,
    and `Website.__company_horizon_clause__` says the same thing on the model).

    Hosting is the one kind where ``NULL`` is a real answer: shared infrastructure belongs to no
    client and stays visible to restricted staff, exactly as §15 says an unattached row does.
    """
    if kind == "website":
        conditions = [_websites.c.org_id == org_id, _websites.c.id.in_(ids)]
        if scope is not None:
            conditions.append(_domains.c.company_id.in_(scope))
        return (
            select(_websites.c.id, _domains.c.company_id)
            .select_from(_websites.join(_domains, _websites.c.domain_id == _domains.c.id))
            .where(*conditions)
        )
    if kind == "domain":
        conditions = [_domains.c.org_id == org_id, _domains.c.id.in_(ids)]
        if scope is not None:
            conditions.append(_domains.c.company_id.in_(scope))
        return select(_domains.c.id, _domains.c.company_id).where(*conditions)
    conditions = [_hosting.c.org_id == org_id, _hosting.c.id.in_(ids)]
    if scope is not None:
        conditions.append(
            _hosting.c.company_id.in_(scope) | _hosting.c.company_id.is_(None)
        )
    return select(_hosting.c.id, _hosting.c.company_id).where(*conditions)


def link_status(
    *,
    website_id: uuid.UUID | None,
    domain_id: uuid.UUID | None,
    hosting_id: uuid.UUID | None,
    candidates: list[Any] | None,
) -> str:
    """Where this monitor stands: attached, proposed, ambiguous, or nothing found.

    Derived from the columns rather than stored beside them, for the reason `sync_status` is
    stored and this is not: that one is what a *remote* read observed and cannot be recomputed,
    while this is a fact about three foreign keys and a list we already hold.
    """
    if website_id is not None or domain_id is not None or hosting_id is not None:
        return LINKED
    count = len(candidates or [])
    if count == 1:
        return MATCHED
    return AMBIGUOUS if count > 1 else UNMATCHED
