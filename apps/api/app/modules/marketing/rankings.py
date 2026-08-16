"""How a client's keyword positions are reported (issue #373).

Until this existed, ``marketing.rankings`` was produced from **SE Ranking and nothing else**.
A client without that subscription got no keyword section at all — silently, with nothing on the
document or on the review screen to say one had been withheld — even though Search Console is
connected for practically every client and answers the question directly. That is the one thing
an agency's customer asks every month: *waar sta ik nu?*

So the section has a **source**, and the source is a setting rather than an accident of which
integration happens to be linked:

    org default (`marketing_settings.rankings`)
        ↓  NULL = inherit, never "unfilled" — the leave-module idiom (CLAUDE.md §14)
    per client  (`marketing_company_settings.rankings`)

``auto`` is the default and does what the name says: SE Ranking where the client has a linked
project, Search Console otherwise. It is deliberately not "SE Ranking, or nothing", because the
whole point is that the fallback exists; and deliberately not "whichever has more keywords",
because a source that silently changes between months makes two reports incomparable.

The rest of the settings are what turns an export into a report. A Search Console property
answers with every term it was ever shown for, thousands of them, most seen twice; printing that
is not reporting. ``limit`` and ``min_impressions`` are the two knobs an agency actually reaches
for, and both have defaults that are right for the agency that never opens this screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RankingSource(StrEnum):
    """Where a client's keyword positions come from."""

    #: SE Ranking where this client has a project, Search Console otherwise. The default,
    #: because it is the only value that is right for a mixed client list without anyone having
    #: to visit a screen.
    AUTO = "auto"
    SERANKING = "seranking"
    SEARCH_CONSOLE = "search_console"
    #: No keyword section for this client. A real answer: an agency that does no SEO for a
    #: client should not send them a ranking table, and switching the *section* off (#373's
    #: per-client section picker) would also be right — this is the narrower statement.
    OFF = "off"


#: A report table is a page of a PDF. Twenty-five terms is a page a client reads; two hundred is
#: an export they scroll past. The cap is reported on the run's warnings, never on the document
#: (CLAUDE.md §17).
DEFAULT_LIMIT = 25
#: Below this a "position" is one or two impressions and a coin toss — Search Console will
#: happily report an average position of 3.0 for a term shown twice, and a table of those buries
#: the terms the client is actually competing for. Ignored for SE Ranking, whose keywords are
#: ones somebody chose to track.
DEFAULT_MIN_IMPRESSIONS = 10
#: How deep a position still counts as *visible*. Deliberately the same number
#: ``SeRankingAdapter.VISIBLE_DEPTH`` uses, and that is the whole point: one section with two
#: possible sources is only honest if both draw the line in the same place. A client whose
#: agency switches source must not find sixty new "rankings" appearing in a month where nothing
#: changed, and a term at 87 is not something anybody can act on this month either way.
DEFAULT_MAX_POSITION = 25

_MAX_LIMIT = 200


@dataclass(frozen=True)
class RankingSettings:
    """The resolved answer for one client — never NULLs, so no caller re-derives inheritance."""

    source: RankingSource = RankingSource.AUTO
    limit: int = DEFAULT_LIMIT
    min_impressions: int = DEFAULT_MIN_IMPRESSIONS
    max_position: int = DEFAULT_MAX_POSITION
    #: Group the table by the keyword group the source knows about. SE Ranking has groups
    #: because somebody made them; Search Console has none, and this setting cannot invent any.
    grouped: bool = True
    #: Print the page that ranked. Only SE Ranking knows it (a Search Console query dimension
    #: says what was searched, not what answered), so this narrows rather than adds.
    show_landing_pages: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "limit": self.limit,
            "min_impressions": self.min_impressions,
            "max_position": self.max_position,
            "grouped": self.grouped,
            "show_landing_pages": self.show_landing_pages,
        }


def _int(raw: Any, fallback: int, *, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(raw)))
    except (TypeError, ValueError):
        return fallback


def _bool(raw: Any, fallback: bool) -> bool:
    return bool(raw) if isinstance(raw, bool) else fallback


def parse(stored: dict[str, Any] | None, *, base: RankingSettings | None = None) -> RankingSettings:
    """One stored blob over a base, every value clamped to something a document can print.

    A field the blob does not mention inherits — that is what makes the per-client row a **diff**
    rather than a snapshot, so raising the house limit reaches every client who never set one
    (the template-layout rule, CLAUDE.md §10, one layer down).
    """
    base = base or RankingSettings()
    if not isinstance(stored, dict):
        return base
    try:
        source = RankingSource(str(stored.get("source")))
    except ValueError:
        source = base.source
    return RankingSettings(
        source=source,
        limit=_int(stored.get("limit"), base.limit, low=1, high=_MAX_LIMIT),
        min_impressions=_int(
            stored.get("min_impressions"), base.min_impressions, low=0, high=10_000
        ),
        max_position=_int(stored.get("max_position"), base.max_position, low=3, high=100),
        grouped=_bool(stored.get("grouped"), base.grouped),
        show_landing_pages=_bool(stored.get("show_landing_pages"), base.show_landing_pages),
    )


def resolve(
    org_stored: dict[str, Any] | None, company_stored: dict[str, Any] | None
) -> RankingSettings:
    """The org's house settings, then this client's own diff over them."""
    return parse(company_stored, base=parse(org_stored))


def effective_source(
    settings: RankingSettings, *, has_seranking: bool, has_search_console: bool
) -> RankingSource | None:
    """Which source will actually be read, or ``None`` for "this client gets no section".

    ``auto`` resolves here rather than at the provider so that one function answers it for the
    gatherer, for the settings screen (which shows the agency what *will* happen) and for the
    section catalog (which tells them whether this client has the data at all). Three copies of
    a preference rule is how a screen comes to promise a section the run does not produce.
    """
    if settings.source is RankingSource.OFF:
        return None
    if settings.source is RankingSource.SERANKING:
        return RankingSource.SERANKING if has_seranking else None
    if settings.source is RankingSource.SEARCH_CONSOLE:
        return RankingSource.SEARCH_CONSOLE if has_search_console else None
    if has_seranking:
        return RankingSource.SERANKING
    return RankingSource.SEARCH_CONSOLE if has_search_console else None


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_POSITION",
    "DEFAULT_MIN_IMPRESSIONS",
    "RankingSettings",
    "RankingSource",
    "effective_source",
    "parse",
    "resolve",
]
