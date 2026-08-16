"""How a client with more than one website is reported (issue #381).

A company is the hub, and an agency attaches to it whatever that client has: AAproTec B.V. has
``aaprotec.nl`` *and* ``opentjewereld.nl``, each with its own GA4 property and its own Search
Console property, all four legitimately on the one company. The dashboard has always shown them
as four cards, named. The report had no answer at all, and the shape of *not* having one was
the worst available:

    link = next(link for link in out.links if link.source == "ga4")   # one arbitrary link
    out.stored[link.source] = ...                                     # …and the *last* one wins

So the live sections (Zoekmachines, Verwijzend verkeer, Socialmedia, Conversies) were computed
from one property and the totals above them from the other, in one document, under one client's
name — and because ``scoped_select()`` carries no ``ORDER BY``, which property won was not
stable between runs. A July report read *google · 14 sessies* against 511 Search Console clicks
for the site it was supposedly about.

There is no default that is right for everybody, which is why this is a setting rather than a
fix:

``per_website``
    One block per property inside each section, named. Two brands under one client stay two
    brands, and a reader can see which numbers belong to which site. The default, because it is
    the answer that never merges two things that should not be merged.

``combined``
    One set of figures over every property. Right for a client whose second property is a
    subdomain, a shop on the same brand, or a migration mid-flight — where two blocks would be
    arithmetic pedantry about one business.

Resolution is the three-layer diff this module already uses for keyword positions: the org's
house rule, then this client's own override, then nothing. ``NULL`` means *inherit* and never
*unset* — the leave-module idiom (CLAUDE.md §14).

``exclude`` is per client only, because a link id is. It is what makes "report on the main site
and leave the campaign microsite out" expressible without unlinking a property the dashboard
still wants.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ReportSplit(StrEnum):
    """Whether a client's properties are reported apart or together."""

    #: One named block per property, inside each section. The default: it is the only value
    #: that cannot silently add two things together that a reader would not have added.
    PER_WEBSITE = "per_website"
    COMBINED = "combined"


@dataclass(frozen=True)
class ReportSettings:
    """The resolved answer for one client — never NULLs, so no caller re-derives inheritance."""

    split: ReportSplit = ReportSplit.PER_WEBSITE
    #: Marketing links this client's report leaves out entirely. Per client, because a link id
    #: is; and an *exclusion* rather than an inclusion so that linking a new property adds it to
    #: the report, which is what somebody linking a property means.
    exclude: frozenset[uuid.UUID] = field(default_factory=frozenset)

    def as_dict(self) -> dict[str, Any]:
        return {
            "split": self.split.value,
            "exclude": sorted(str(link_id) for link_id in self.exclude),
        }


def _ids(raw: Any) -> frozenset[uuid.UUID] | None:
    if not isinstance(raw, list):
        return None
    out: set[uuid.UUID] = set()
    for item in raw:
        try:
            out.add(uuid.UUID(str(item)))
        except (TypeError, ValueError):
            # A link id that no longer parses is a link that no longer exists. Dropping it is
            # the same answer as excluding nothing, and raising would make a stale setting take
            # a client's whole report down.
            continue
    return frozenset(out)


def parse(stored: dict[str, Any] | None, *, base: ReportSettings | None = None) -> ReportSettings:
    """One stored blob over a base, every field a diff — ``rankings.parse``'s contract."""
    base = base or ReportSettings()
    if not isinstance(stored, dict):
        return base
    try:
        split = ReportSplit(str(stored.get("split")))
    except ValueError:
        split = base.split
    exclude = _ids(stored.get("exclude"))
    return ReportSettings(split=split, exclude=base.exclude if exclude is None else exclude)


def resolve(
    org_stored: dict[str, Any] | None, company_stored: dict[str, Any] | None
) -> ReportSettings:
    """The org's house rule, then this client's own diff over it."""
    return parse(company_stored, base=parse(org_stored))


__all__ = ["ReportSettings", "ReportSplit", "parse", "resolve"]
