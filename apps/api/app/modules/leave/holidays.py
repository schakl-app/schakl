"""Public-holiday generators (#47).

**Derive, don't paste.** A table of 2026 and 2027 is right for two years and wrong forever
after. Easter drives half the Dutch calendar, so compute it (anonymous Gregorian algorithm)
and everything else falls out — 2028, 2030 and the Koningsdag-on-a-Sunday shift included.

**Seed everything; decide nothing.** Goede Vrijdag is worked at many Dutch employers and
Bevrijdingsdag is a paid day off only every fifth year under a lot of CAOs. Which of these is
a day off is the tenant's answer, not Python's — the generator emits the whole list and the
tenant deactivates what they work (CLAUDE.md §14: *"don't hardcode any country's law"*).

``source`` is a country code, not a feature flag: adding ``be`` or ``de`` later is a new
function in this module, not a schema change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

#: The one country shipped today. ``manual`` is reserved for tenant-added rows.
COUNTRY_NL = "nl"
SOURCE_MANUAL = "manual"


@dataclass(frozen=True)
class GeneratedHoliday:
    """One holiday for one year. ``key`` is stable across years, so a moved date *moves*."""

    key: str
    day: date
    name_i18n: dict[str, str]


def easter_sunday(year: int) -> date:
    """Easter Sunday in the Gregorian calendar (anonymous Gregorian algorithm).

    5 April 2026, 28 March 2027, 16 April 2028 — the dates the rest of the calendar hangs off.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - the algorithm's own variable names
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def kingsday(year: int) -> date:
    """27 April, moved **back** to the 26th when the 27th is a Sunday (Dutch law)."""
    day = date(year, 4, 27)
    return day - timedelta(days=1) if day.weekday() == 6 else day


def dutch_holidays(year: int) -> list[GeneratedHoliday]:
    """Every Dutch public holiday for ``year``, days off or not — the tenant decides."""
    easter = easter_sunday(year)
    day = timedelta(days=1)
    return [
        GeneratedHoliday(
            "nieuwjaarsdag", date(year, 1, 1), {"nl": "Nieuwjaarsdag", "en": "New Year's Day"}
        ),
        GeneratedHoliday(
            "goede_vrijdag", easter - 2 * day, {"nl": "Goede Vrijdag", "en": "Good Friday"}
        ),
        GeneratedHoliday(
            "eerste_paasdag", easter, {"nl": "Eerste Paasdag", "en": "Easter Sunday"}
        ),
        GeneratedHoliday(
            "tweede_paasdag", easter + day, {"nl": "Tweede Paasdag", "en": "Easter Monday"}
        ),
        GeneratedHoliday("koningsdag", kingsday(year), {"nl": "Koningsdag", "en": "King's Day"}),
        GeneratedHoliday(
            "bevrijdingsdag", date(year, 5, 5), {"nl": "Bevrijdingsdag", "en": "Liberation Day"}
        ),
        GeneratedHoliday(
            "hemelvaartsdag", easter + 39 * day, {"nl": "Hemelvaartsdag", "en": "Ascension Day"}
        ),
        GeneratedHoliday(
            "eerste_pinksterdag",
            easter + 49 * day,
            {"nl": "Eerste Pinksterdag", "en": "Whit Sunday"},
        ),
        GeneratedHoliday(
            "tweede_pinksterdag",
            easter + 50 * day,
            {"nl": "Tweede Pinksterdag", "en": "Whit Monday"},
        ),
        GeneratedHoliday(
            "eerste_kerstdag", date(year, 12, 25), {"nl": "Eerste Kerstdag", "en": "Christmas Day"}
        ),
        GeneratedHoliday(
            "tweede_kerstdag", date(year, 12, 26), {"nl": "Tweede Kerstdag", "en": "Boxing Day"}
        ),
    ]


#: Country code → generator. One entry today; the column exists so a second is a function.
GENERATORS: dict[str, Callable[[int], list[GeneratedHoliday]]] = {COUNTRY_NL: dutch_holidays}


def generate(country: str, year: int) -> list[GeneratedHoliday]:
    generator = GENERATORS.get(country)
    return generator(year) if generator else []
