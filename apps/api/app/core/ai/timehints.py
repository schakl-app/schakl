"""What a quick-add line says about *when*, read without a model.

The time parse (``features.parse_time_entry``) hands one line to a model and fills a form
from its one tool call. When that call does not come — the model answered in prose, spent its
token ceiling on reasoning, or was cut off — every field came back null and the user read
*"Kon hier geen registratie uit afleiden"* over a line that plainly said ``14:00-16:30``. A
clock time is not something a language model is needed for, so this module reads the parts of
the line that have exactly one meaning and the parse uses them to fill whatever the model
left blank. The model's answer still wins wherever it gave one: a hint fills a gap, it never
overrules.

Deliberately narrow. Every rule here is one a Dutch or English speaker would call unambiguous
in a *time entry* — ``gisteren``, ``afgelopen vrijdag``, ``14:00-16:30``, ``2 uur``, ``90 min``
— and a shape with two plausible readings is left alone rather than guessed at: a wrong span
prefilled into the form is worse than an empty one, because it looks like an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_WEEKDAYS: dict[str, int] = {
    "maandag": 0,
    "dinsdag": 1,
    "woensdag": 2,
    "donderdag": 3,
    "vrijdag": 4,
    "zaterdag": 5,
    "zondag": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_RELATIVE_DAYS: dict[str, int] = {
    "vandaag": 0,
    "today": 0,
    "gisteren": -1,
    "yesterday": -1,
    "eergisteren": -2,
    "morgen": 1,
    "tomorrow": 1,
}

#: Number words a person says for a duration. ``een``/``één`` is included because "een uur"
#: is how most people say sixty minutes; the article reading ("een uur of drie") is too rare
#: in a time line to cost a rule.
_NUMBER_WORDS: dict[str, float] = {
    "een": 1,
    "één": 1,
    "an": 1,
    "one": 1,
    "twee": 2,
    "two": 2,
    "drie": 3,
    "three": 3,
    "vier": 4,
    "four": 4,
    "vijf": 5,
    "five": 5,
    "zes": 6,
    "six": 6,
    "zeven": 7,
    "seven": 7,
    "acht": 8,
    "eight": 8,
    "anderhalf": 1.5,
    "half": 0.5,
}

_WEEKDAY_RE = re.compile(
    r"\b(?P<prefix>afgelopen|vorige|last|this|deze)?\s*(?P<day>" + "|".join(_WEEKDAYS) + r")\b",
    re.IGNORECASE,
)
_RELATIVE_RE = re.compile(r"\b(" + "|".join(_RELATIVE_DAYS) + r")\b", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_EU_DATE_RE = re.compile(r"\b(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?\b")

#: ``14:00-16:30``, ``14.00 - 16.30``, ``van 14:00 tot 16:30``, ``9-11:30``, ``14u-16u``.
_SPAN_RE = re.compile(
    r"(?<![\d:])(?:van\s+|from\s+)?"
    r"(?P<h1>\d{1,2})(?:[:.u](?P<m1>\d{2})|\s*(?:u|uur)\b)?"
    r"\s*(?:-|–|—|tot|to|t/m|until|till)\s*"
    r"(?P<h2>\d{1,2})(?:[:.u](?P<m2>\d{2})|\s*(?:u|uur)\b)?"
    r"(?![\d:-])",
    re.IGNORECASE,
)

#: ``2 uur``, ``1,5 uur``, ``2.5h``, ``90 min``, ``1u30``, ``twee uur``, ``anderhalf uur``.
_HOURS_RE = re.compile(
    r"(?<![\d:])(?P<n>\d+(?:[.,]\d+)?|\b(?:"
    + "|".join(_NUMBER_WORDS)
    + r"))\s*(?P<unit>uur|u|h|hours?|hrs?)\b(?!\s*(?:tot|to|-|–))",
    re.IGNORECASE,
)
_H_M_RE = re.compile(r"(?<![\d:])(?P<h>\d{1,2})u(?P<m>\d{2})\b", re.IGNORECASE)
#: "een kwartier" carries its own unit.
_QUARTER_RE = re.compile(r"\bkwartier(?:tje)?\b", re.IGNORECASE)
_MINUTES_RE = re.compile(
    r"(?<![\d:])(?P<n>\d+)\s*(?:min|minuten|minutes?|mins?|m)\b(?!\s*(?:tot|to|-|–))",
    re.IGNORECASE,
)
#: A duration that is a *break* is not the entry's length.
_BREAK_NEAR_RE = re.compile(r"\b(pauze|break|lunch)\b", re.IGNORECASE)
#: "om 14 uur", "vanaf 9 uur": a clock time wearing the duration's clothes.
_CLOCK_PREFIX_RE = re.compile(r"\b(om|vanaf|van|tot|at|from|until)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class TimeHints:
    """The when-parts of one line, each ``None`` where the line does not say."""

    date: date | None = None
    start: str | None = None
    end: str | None = None
    duration_minutes: int | None = None


def _hhmm(hours: int, minutes: int) -> str | None:
    if hours > 23 or minutes > 59:
        return None
    return f"{hours:02d}:{minutes:02d}"


def _date_hint(text: str, today: date) -> date | None:
    match = _ISO_DATE_RE.search(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    match = _RELATIVE_RE.search(text)
    if match:
        return today + timedelta(days=_RELATIVE_DAYS[match.group(1).lower()])
    match = _WEEKDAY_RE.search(text)
    if match:
        wanted = _WEEKDAYS[match.group("day").lower()]
        back = (today.weekday() - wanted) % 7
        prefix = (match.group("prefix") or "").lower()
        # "afgelopen vrijdag" on a Friday is last week's; a bare "vrijdag" is today's.
        if back == 0 and prefix in {"afgelopen", "vorige", "last"}:
            back = 7
        return today - timedelta(days=back)
    for match in _EU_DATE_RE.finditer(text):
        day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
        # ``14-16`` is a span, not the 14th of the 16th month; only a real month reads as a
        # date, and without a year it is this year's (or last year's, when that is still ahead).
        if not 1 <= month <= 12 or not 1 <= day <= 31:
            continue
        if year is None and _looks_like_span(day, month):
            continue
        try:
            if year is not None:
                full = int(year) if len(year) == 4 else 2000 + int(year)
                return date(full, month, day)
            candidate = date(today.year, month, day)
        except ValueError:
            continue
        if candidate > today + timedelta(days=7):
            candidate = date(today.year - 1, month, day)
        return candidate
    return None


def _looks_like_span(first: int, second: int) -> bool:
    """``10-12`` is far more often ten-to-twelve than the tenth of December in a time line."""
    return 6 <= first < second <= 23


def _span_hint(text: str) -> tuple[str | None, str | None]:
    for match in _SPAN_RE.finditer(text):
        h1, h2 = int(match.group("h1")), int(match.group("h2"))
        m1, m2 = match.group("m1"), match.group("m2")
        explicit = m1 is not None or m2 is not None or "u" in match.group(0).lower()
        wordy = bool(re.search(r"\b(van|from|tot|to|until|till|t/m)\b", match.group(0), re.I))
        # A bare ``14-16`` is a span only when it cannot be a date and reads as working hours;
        # ``03-06`` is left to the model.
        if not explicit and not wordy and not _looks_like_span(h1, h2):
            continue
        start = _hhmm(h1, int(m1 or 0))
        end = _hhmm(h2, int(m2 or 0))
        if start is None or end is None:
            continue
        return start, end
    return None, None


def _duration_hint(text: str) -> int | None:
    match = _H_M_RE.search(text)
    if match:
        return int(match.group("h")) * 60 + int(match.group("m"))
    for match in _HOURS_RE.finditer(text):
        raw = match.group("n").lower()
        before = text[: match.start()]
        if _CLOCK_PREFIX_RE.search(before[-8:]):
            continue
        if raw in _NUMBER_WORDS:
            hours = _NUMBER_WORDS[raw]
        else:
            hours = float(raw.replace(",", "."))
            # "14 uur" is two o'clock in the afternoon, never a fourteen-hour day.
            if hours >= 13 and hours == int(hours):
                continue
        after = text[match.end() : match.end() + 12]
        if _BREAK_NEAR_RE.search(after):
            continue
        minutes = int(round(hours * 60))
        return minutes if minutes > 0 else None
    for match in _MINUTES_RE.finditer(text):
        after = text[match.end() : match.end() + 12]
        if _BREAK_NEAR_RE.search(after):
            continue
        minutes = int(match.group("n"))
        return minutes if 0 < minutes <= 24 * 60 else None
    match = _QUARTER_RE.search(text)
    if match and not _BREAK_NEAR_RE.search(text[match.end() : match.end() + 12]):
        return 15
    return None


def local_hints(text: str, *, today: date) -> TimeHints:
    """Everything about *when* that one line states without ambiguity."""
    start, end = _span_hint(text)
    return TimeHints(
        date=_date_hint(text, today),
        start=start,
        end=end,
        duration_minutes=_duration_hint(text) if start is None else None,
    )


__all__ = ["TimeHints", "local_hints"]
