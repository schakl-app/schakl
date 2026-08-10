"""What a period is measured against — one answer, shared (issue #312).

A number without a comparison says nothing, and *which* comparison is a product decision that
two modules had already taken separately: ``reporting`` compares a month to the same month a
year earlier (#300), while the ``marketing`` dashboard silently compared to the immediately
preceding span and labelled every delta "t.o.v. vorige periode". The same client's July could
therefore be up 12% in the PDF and down 4% on the screen the PDF was built from, with nothing
on either surface saying why.

So the vocabulary and the date math live here, once:

- :class:`ComparePeriod` — ``year`` (the same span a year earlier) or ``previous`` (the span
  immediately before). ``year`` is the default everywhere: it is the comparison a client asks
  about and the one that survives seasonality — a campsite's July has nothing to say to its
  June.
- :func:`compare_window` — the span itself, with the two rules that are easy to get wrong.

Both rules exist because subtracting days is not the same as stepping back a period:

- **A whole month compares to a whole month.** Subtracting 31 days from 1 July lands on 31 May,
  so a naive "previous period" would straddle two months and be neither.
- **29 February has no counterpart.** Stepping a leap day back a year raises; it lands on the
  28th rather than refusing to compare at all.

A caller that stores the mode stores :class:`ComparePeriod`'s *value*, never its name, so the
string in a JSONB blob or a column reads the same as the one on the wire.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from enum import StrEnum

_ONE_DAY = timedelta(days=1)


class ComparePeriod(StrEnum):
    """Which span a period is measured against."""

    #: The same span a year earlier. What a client asks about ("is dit beter dan vorig jaar?")
    #: and what survives seasonality — a campsite's July is not comparable to its June.
    YEAR = "year"
    #: The span immediately before this one. For a business without a season, and for a property
    #: too young to have a year of history to compare against.
    PREVIOUS = "previous"


#: The mode a surface uses when nothing has been configured — stated once so "the default" is
#: not re-decided per module.
DEFAULT_COMPARE = ComparePeriod.YEAR


def resolve_compare(*values: str | None) -> ComparePeriod:
    """The first configured mode in ``values``, else :data:`DEFAULT_COMPARE`.

    Written as a fallback chain because that is what every caller has: a per-client override,
    then the org's default, then ours. ``None`` and the empty string both mean *inherit*, and an
    unrecognised stored string falls through rather than raising — a mode that vanished in a
    later release must not 500 a dashboard.
    """
    for value in values:
        if not value:
            continue
        try:
            return ComparePeriod(value)
        except ValueError:
            continue
    return DEFAULT_COMPARE


def is_whole_month(start: date, end: date) -> bool:
    """Whether ``[start, end]`` is exactly one calendar month."""
    return (
        start.day == 1
        and start.year == end.year
        and start.month == end.month
        and end.day == monthrange(end.year, end.month)[1]
    )


def _a_year_earlier(value: date) -> date:
    """``value`` one year back — resolved **per endpoint**, which is the whole point.

    Only 29 February has no counterpart, and only that day may move. Stepping the pair together
    inside one ``try`` (the shape this was lifted from) let a leap-day *start* drag the end of
    the span with it: 29 Feb – 31 Mar came back as 28 Feb – **28** Mar, three days of the
    comparison quietly gone.
    """
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def compare_window(start: date, end: date, mode: str | ComparePeriod) -> tuple[date, date]:
    """The span ``[start, end]`` is measured against under ``mode``.

    Always a real span: a caller that has nothing to compare against learns it from the *data*
    (no rows in the window), not from a ``None`` here — which is what lets every screen name the
    period it used even when the number underneath it is zero.
    """
    if ComparePeriod(mode) is ComparePeriod.PREVIOUS:
        compare_end = start - _ONE_DAY
        if is_whole_month(start, end):
            return compare_end.replace(day=1), compare_end
        return compare_end - (end - start), compare_end
    return _a_year_earlier(start), _a_year_earlier(end)
