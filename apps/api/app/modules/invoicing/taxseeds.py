"""Per-country tax-rate starter sets (issue #207) — the ``leave_holidays`` rule for VAT.

These seed ``invoicing_tax_rates`` for the org's tax country: **derived suggestions of
today's common rates, never law in code**. The tenant renames, re-rates, deactivates and
extends freely, and a seed run never resurrects or overwrites what they changed (it only
fills an *empty* catalog). A country not listed gets the generic set — a zero rate and the
cross-border categories — because inventing rates for 190 jurisdictions would be exactly the
hardcoding this file exists to avoid.

Every set carries the two special categories the engine treats distinctly whatever the
country: ``exempt`` (out of scope, charges nothing) and ``reverse_charge`` (intra-EU B2B
"btw verlegd": 0 on the document, category printed and coded in UBL).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypedDict

from app.modules.invoicing.models import TaxCategory


class TaxSeed(TypedDict):
    label_i18n: dict[str, str]
    rate: Decimal
    category: str
    is_default: bool
    position: int


def _seed(
    nl: str, en: str, rate: str, category: TaxCategory, *, default: bool = False, pos: int = 0
) -> TaxSeed:
    return TaxSeed(
        label_i18n={"nl": nl, "en": en},
        rate=Decimal(rate),
        category=category.value,
        is_default=default,
        position=pos,
    )


_COMMON_TAIL = [
    _seed("Vrijgesteld", "Exempt", "0", TaxCategory.EXEMPT, pos=80),
    _seed("Btw verlegd", "Reverse charged", "0", TaxCategory.REVERSE_CHARGE, pos=90),
]

#: Keyed by ISO 3166-1 alpha-2 of the org's tax country.
TAX_SEEDS: dict[str, list[TaxSeed]] = {
    "NL": [
        _seed("21% hoog", "21% standard", "21", TaxCategory.STANDARD, default=True, pos=10),
        _seed("9% laag", "9% reduced", "9", TaxCategory.REDUCED, pos=20),
        _seed("0% nultarief", "0% zero rate", "0", TaxCategory.ZERO, pos=30),
        *_COMMON_TAIL,
    ],
    "BE": [
        _seed("21% standaard", "21% standard", "21", TaxCategory.STANDARD, default=True, pos=10),
        _seed("12% verlaagd", "12% reduced", "12", TaxCategory.REDUCED, pos=20),
        _seed("6% verlaagd", "6% reduced", "6", TaxCategory.REDUCED, pos=30),
        _seed("0% nultarief", "0% zero rate", "0", TaxCategory.ZERO, pos=40),
        *_COMMON_TAIL,
    ],
    "DE": [
        _seed("19% Regelsatz", "19% standard", "19", TaxCategory.STANDARD, default=True, pos=10),
        _seed("7% ermäßigt", "7% reduced", "7", TaxCategory.REDUCED, pos=20),
        _seed("0% nultarief", "0% zero rate", "0", TaxCategory.ZERO, pos=30),
        *_COMMON_TAIL,
    ],
    "FR": [
        _seed("20% normaal", "20% standard", "20", TaxCategory.STANDARD, default=True, pos=10),
        _seed("10% verlaagd", "10% reduced", "10", TaxCategory.REDUCED, pos=20),
        _seed("5,5% verlaagd", "5.5% reduced", "5.5", TaxCategory.REDUCED, pos=30),
        _seed("2,1% bijzonder", "2.1% super-reduced", "2.1", TaxCategory.REDUCED, pos=40),
        *_COMMON_TAIL,
    ],
    "ES": [
        _seed("21% algemeen", "21% standard", "21", TaxCategory.STANDARD, default=True, pos=10),
        _seed("10% verlaagd", "10% reduced", "10", TaxCategory.REDUCED, pos=20),
        _seed("4% superverlaagd", "4% super-reduced", "4", TaxCategory.REDUCED, pos=30),
        *_COMMON_TAIL,
    ],
    "IT": [
        _seed("22% standaard", "22% standard", "22", TaxCategory.STANDARD, default=True, pos=10),
        _seed("10% verlaagd", "10% reduced", "10", TaxCategory.REDUCED, pos=20),
        _seed("5% verlaagd", "5% reduced", "5", TaxCategory.REDUCED, pos=30),
        _seed("4% minimum", "4% super-reduced", "4", TaxCategory.REDUCED, pos=40),
        *_COMMON_TAIL,
    ],
    "AT": [
        _seed("20% Normalsatz", "20% standard", "20", TaxCategory.STANDARD, default=True, pos=10),
        _seed("13% ermäßigt", "13% reduced", "13", TaxCategory.REDUCED, pos=20),
        _seed("10% ermäßigt", "10% reduced", "10", TaxCategory.REDUCED, pos=30),
        *_COMMON_TAIL,
    ],
    "GB": [
        _seed("20% standard", "20% standard", "20", TaxCategory.STANDARD, default=True, pos=10),
        _seed("5% reduced", "5% reduced", "5", TaxCategory.REDUCED, pos=20),
        _seed("0% zero rate", "0% zero rate", "0", TaxCategory.ZERO, pos=30),
        *_COMMON_TAIL,
    ],
    "CH": [
        _seed("8,1% normaal", "8.1% standard", "8.1", TaxCategory.STANDARD, default=True, pos=10),
        _seed("3,8% logies", "3.8% lodging", "3.8", TaxCategory.REDUCED, pos=20),
        _seed("2,6% verlaagd", "2.6% reduced", "2.6", TaxCategory.REDUCED, pos=30),
        *_COMMON_TAIL,
    ],
    #: No federal VAT — agencies handle sales tax per state, which is theirs to configure.
    "US": [
        _seed("Geen belasting", "No tax", "0", TaxCategory.ZERO, default=True, pos=10),
        _seed("Vrijgesteld", "Exempt", "0", TaxCategory.EXEMPT, pos=20),
    ],
}

#: Any other country: a neutral start the tenant builds on.
GENERIC_SEEDS: list[TaxSeed] = [
    _seed("Standaardtarief", "Standard rate", "0", TaxCategory.STANDARD, default=True, pos=10),
    _seed("0% nultarief", "0% zero rate", "0", TaxCategory.ZERO, pos=20),
    *_COMMON_TAIL,
]


def seeds_for(country: str | None) -> list[TaxSeed]:
    return TAX_SEEDS.get((country or "").upper(), GENERIC_SEEDS)
