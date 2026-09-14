"""The when-parts of a quick-add line, read without a model (``app/core/ai/timehints.py``).

Pure: no tenant, no provider. Every case is one a person would call unambiguous in a time
line, and the negative cases are the shapes that have two readings and must stay ``None``.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.ai.timehints import local_hints

TODAY = date(2026, 9, 11)  # a Friday


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("gisteren 14:00-16:30 website Jansen", date(2026, 9, 10)),
        ("eergisteren 2 uur", date(2026, 9, 9)),
        ("vandaag overleg", TODAY),
        ("yesterday 2h Nova", date(2026, 9, 10)),
        ("afgelopen vrijdag 1,5 uur", date(2026, 9, 4)),
        ("vrijdag 90 min", TODAY),
        ("maandag van 9 tot 11", date(2026, 9, 7)),
        ("last monday call", date(2026, 9, 7)),
        ("03-06-2026 2 uur", date(2026, 6, 3)),
        ("2026-09-01 3 uur", date(2026, 9, 1)),
        ("op 3/9 overleg", date(2026, 9, 3)),
        ("2 uur Jansen", None),
    ],
)
def test_dates(line: str, expected: date | None) -> None:
    assert local_hints(line, today=TODAY).date == expected


@pytest.mark.parametrize(
    ("line", "start", "end"),
    [
        ("gisteren 14:00-16:30 website", "14:00", "16:30"),
        ("14.00 - 16.30 overleg", "14:00", "16:30"),
        ("van 9 tot 11 overleg", "09:00", "11:00"),
        ("14u-16u30 Jansen", "14:00", "16:30"),
        ("9-11:30 bouwen", "09:00", "11:30"),
        ("10-12 overleg", "10:00", "12:00"),
        # A date, not a span: the tenth of December is not ten-to-twelve backwards.
        ("03-06 overleg", None, None),
        ("2 uur Jansen", None, None),
    ],
)
def test_spans(line: str, start: str | None, end: str | None) -> None:
    hints = local_hints(line, today=TODAY)
    assert (hints.start, hints.end) == (start, end)


@pytest.mark.parametrize(
    ("line", "minutes"),
    [
        ("2 uur Jansen", 120),
        ("1,5 uur overleg", 90),
        ("2.5h Nova", 150),
        ("90 min rapport", 90),
        ("1u30 rapport", 90),
        ("anderhalf uur Nova", 90),
        ("twee uur website", 120),
        ("een uur bellen", 60),
        ("kwartier mailen", 15),
        # A clock time wearing the duration's clothes.
        ("om 14 uur gebeld met Jansen", None),
        ("14 uur gebeld", None),
        # A break is not the entry's length, and a span makes a duration redundant.
        ("half uur pauze, 14:00-18:00", None),
        ("30 min lunch", None),
        ("vandaag 30 min lunch en 3 uur bouwen", 180),
        ("plan uur", None),
    ],
)
def test_durations(line: str, minutes: int | None) -> None:
    assert local_hints(line, today=TODAY).duration_minutes == minutes
