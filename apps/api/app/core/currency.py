"""Org currency (issue #124, CLAUDE.md §8) — a per-org business fact, like the timezone.

The active ISO 4217 alphabetic codes, so a typo ("EUO") is rejected on write while any real
currency a non-euro agency runs in is accepted. Kept as data here rather than pulled from a
library: the list changes on the order of once a decade, and validation must not grow a
dependency. Display formatting stays the client's job (``Intl`` knows each currency's symbol
and fraction digits); the API only stores and validates the code.
"""

from __future__ import annotations

DEFAULT_CURRENCY = "EUR"

#: Active ISO 4217 alphabetic codes (funds/testing/metal codes excluded).
ISO_4217: frozenset[str] = frozenset(
    {
        "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
        "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
        "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
        "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
        "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GIP", "GMD",
        "GNF", "GTQ", "GYD", "HKD", "HNL", "HTG", "HUF", "IDR", "ILS", "INR",
        "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR", "KMF",
        "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL",
        "LYD", "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU", "MUR",
        "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK", "NPR",
        "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG", "QAR",
        "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK", "SGD",
        "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SVC", "SYP", "SZL", "THB",
        "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX",
        "USD", "UYU", "UZS", "VED", "VES", "VND", "VUV", "WST", "XAF", "XCD",
        "XOF", "XPF", "YER", "ZAR", "ZMW", "ZWG",
    }
)


def is_valid_currency(code: str | None) -> bool:
    return bool(code) and code in ISO_4217
