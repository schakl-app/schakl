"""What a period *is*, and what it is measured against — one answer, shared (issues #312, #316).

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

**The period itself lives here too** (#316), for the same reason the comparison does. A dashboard
that can only say "the last N days" cannot answer the question an agency is actually asked — "how
did July go?" — and a trailing window is a *different question* from a calendar month: 30 days
back from 9 August is 11 July to 9 August, which is not a month anyone reports on. So
:func:`resolve_period` reads one token (``30d``, ``month``, ``last_month``, ``quarter``,
``last_quarter``, ``2026-07``, ``2026-Q3``) and answers with two dates, and everything downstream
— the label, the comparison, the SQL — takes the dates. Nothing but this function knows what a
token means, which is what keeps the browser out of the date business (#312): the resolution needs
the tenant's timezone, and a browser guessing "today" from its own clock is how a dashboard in
Lisbon showed a different July from the same dashboard in Warsaw.

Every span ends **yesterday at the latest**. Today is partial, and comparing fourteen hours
against twenty-four reads as a collapse in traffic every morning.
"""

from __future__ import annotations

import re
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


def is_whole_quarter(start: date, end: date) -> bool:
    """Whether ``[start, end]`` is exactly one calendar quarter.

    Used by the screen to name a span "Q3 2025" rather than printing its two dates. Deliberately
    strict about both ends: a quarter-to-date is *not* a quarter, and labelling it as one would
    put "Q3 2025" over eleven days of it.
    """
    if start.year != end.year or start.day != 1:
        return False
    q_start, q_end = _quarter_span(start.year, quarter_of(start))
    return start == q_start and end == q_end


def quarter_of(value: date) -> int:
    """Which calendar quarter (1-4) ``value`` falls in."""
    return (value.month - 1) // 3 + 1


def _month_span(year: int, month: int) -> tuple[date, date]:
    """The whole calendar month, both ends."""
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _quarter_span(year: int, quarter: int) -> tuple[date, date]:
    """The whole calendar quarter, both ends."""
    first = 3 * (quarter - 1) + 1
    return date(year, first, 1), _month_span(year, first + 2)[1]


def _previous_month(value: date) -> tuple[date, date]:
    """The whole calendar month before the one ``value`` is in."""
    last = value.replace(day=1) - _ONE_DAY
    return _month_span(last.year, last.month)


def _previous_quarter(value: date) -> tuple[date, date]:
    """The whole calendar quarter before the one ``value`` is in."""
    quarter = quarter_of(value)
    return (
        _quarter_span(value.year - 1, 4) if quarter == 1 else _quarter_span(value.year, quarter - 1)
    )


def _to_date(span: tuple[date, date], yesterday: date, fallback: tuple[date, date]) -> tuple[
    date, date
]:
    """``span`` clipped to what has actually finished, or ``fallback`` when nothing has.

    A period-to-date on the first day of that period contains no complete day at all — "deze
    maand" on 1 August is an empty span, and an empty span is not something a chart, a delta or a
    label can be built out of. It resolves to the previous whole period instead, which is both the
    useful answer and a safe one *because the payload carries its dates*: the screen names the
    span it was given ("juli 2025"), so nothing anywhere claims to be showing August.
    """
    start, end = span
    return (start, min(end, yesterday)) if yesterday >= start else fallback


class PeriodPreset(StrEnum):
    """The named spans a screen offers, beyond a plain trailing ``<n>d``."""

    #: This calendar month so far (1st → yesterday).
    MONTH = "month"
    #: The previous whole calendar month — the one a client is usually asking about.
    LAST_MONTH = "last_month"
    #: This calendar quarter so far.
    QUARTER = "quarter"
    #: The previous whole calendar quarter.
    LAST_QUARTER = "last_quarter"


#: What a screen falls back to when no period was asked for: the last 30 complete days.
DEFAULT_PERIOD = "30d"

# Deliberately wider than ``max_days``: a token that parses is *clamped*, and one that does not
# falls back to the default. A three-digit ceiling here would have made "1000d" silently mean
# thirty days while "400d" meant four hundred — the cap has to be the cap, not the regex.
_DAYS_RE = re.compile(r"^(\d{1,6})d$")
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_QUARTER_RE = re.compile(r"^(\d{4})-[Qq]([1-4])$")

#: Tokens kept alive because they are in URLs people have shared and bookmarked (§9: the URL
#: *is* the view). They are spellings of a trailing window, not periods of their own.
_ALIASES = {"yoy": "365d", "year": "365d"}


def resolve_period(token: str | None, today: date, *, max_days: int = 400) -> tuple[date, date]:
    """The span ``token`` names, ending yesterday at the latest.

    ``today`` is the **org's** today (``app.core.timezone.org_today``), never the server's — the
    same rule §8 states for every other wall-clock question. ``max_days`` caps a trailing window
    so a hand-typed ``9999d`` cannot ask the database for a decade.

    An unparseable token falls back to :data:`DEFAULT_PERIOD` rather than raising: a period is a
    *view*, arriving from a query string that anyone can edit or that an old bookmark can carry,
    and a dashboard that 422s on a stale link is worse than one that shows its default.
    """
    yesterday = today - _ONE_DAY
    raw = (token or DEFAULT_PERIOD).strip().lower()
    raw = _ALIASES.get(raw, raw)

    if match := _DAYS_RE.match(raw):
        days = max(1, min(int(match.group(1)), max_days))
        return yesterday - timedelta(days=days - 1), yesterday

    if match := _MONTH_RE.match(raw):
        span = _month_span(int(match.group(1)), int(match.group(2)))
        # An explicitly *named* month is never substituted for another one, which is why the
        # fallback here is the span itself: pick a month that has not started and you get an
        # empty chart labelled with the month you picked, not a silent jump to a different one.
        return _to_date(span, yesterday, span)

    if match := _QUARTER_RE.match(raw):
        span = _quarter_span(int(match.group(1)), int(match.group(2)))
        return _to_date(span, yesterday, span)

    match raw:
        case PeriodPreset.MONTH:
            return _to_date(_month_span(today.year, today.month), yesterday, _previous_month(today))
        case PeriodPreset.LAST_MONTH:
            return _previous_month(today)
        case PeriodPreset.QUARTER:
            span = _quarter_span(today.year, quarter_of(today))
            return _to_date(span, yesterday, _previous_quarter(today))
        case PeriodPreset.LAST_QUARTER:
            return _previous_quarter(today)

    if raw != DEFAULT_PERIOD:
        return resolve_period(DEFAULT_PERIOD, today, max_days=max_days)
    return yesterday - timedelta(days=29), yesterday


def period_days(start: date, end: date) -> int:
    """How many days ``[start, end]`` spans, both ends included."""
    return (end - start).days + 1


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
