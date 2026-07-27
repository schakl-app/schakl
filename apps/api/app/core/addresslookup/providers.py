"""Lookup providers (#241). PDOK first; the interface is what keeps Mapbox addable.

Follows ``app.modules.domains.dns``'s outbound-lookup rules: short timeout, fail soft. A
slow or down provider must never block the company form — an empty result reads as "no
suggestion", never as an error banner. The target host is fixed (a public government API),
so no SSRF guard applies; never route a tenant-supplied URL through here without one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger("schakl.addresslookup")

_LOOKUP_TIMEOUT = httpx.Timeout(4.0)
_MAX_SUGGESTIONS = 5

#: PDOK Locatieserver, the BAG's free, keyless geocoder (https://api.pdok.nl).
PDOK_BASE_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1"

#: A complete Dutch postcode ("1234 AB" in any spacing/casing). PDOK covers only NL, so a
#: postcode that cannot be Dutch is answered locally with "no suggestion" — no network call.
_NL_POSTCODE = re.compile(r"^[1-9][0-9]{3}\s?[A-Za-z]{2}$")


@dataclass(frozen=True)
class AddressSuggestion:
    """One resolved address. ``house_number`` is the full BAG number including letter and
    addition ("10", "10A", "10-2"), so the caller can compose an address line verbatim."""

    street: str
    house_number: str
    postal_code: str
    city: str
    country: str


class AddressLookupProvider(Protocol):
    """One geocoding backend. ``lookup`` returns best matches first and never raises —
    a failure is an empty list (the caller records nothing; there is nothing to record)."""

    async def lookup(self, postal_code: str, house_number: str) -> list[AddressSuggestion]: ...


class PdokProvider:
    """Postcode + huisnummer against the BAG — authoritative for NL, no credentials.

    ``transport`` exists so tests stub the wire instead of the network (the ``dns.py`` rule:
    outbound lookups are isolated and stubbable)."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def lookup(self, postal_code: str, house_number: str) -> list[AddressSuggestion]:
        postal_code = postal_code.strip()
        house_number = house_number.strip()
        digits = re.match(r"\d+", house_number)
        if not _NL_POSTCODE.match(postal_code) or digits is None:
            return []
        # A fielded query, not free text: "2513AA 1" as loose terms ranks 370k docs and hopes;
        # postcode+huisnummer returns exactly that postcode's number (plus its letter variants,
        # which is what the suggestion list is for). The letter/addition the user typed can't go
        # in the query (``huisnummer`` is numeric in the BAG), so an exact match is *preferred*
        # after the fact instead.
        params = {
            "q": f"postcode:{postal_code.replace(' ', '').upper()} and huisnummer:{digits.group()}",
            "fq": "type:adres",
            "fl": "straatnaam huis_nlt postcode woonplaatsnaam",
            "rows": str(_MAX_SUGGESTIONS),
        }
        try:
            async with httpx.AsyncClient(
                timeout=_LOOKUP_TIMEOUT, transport=self._transport
            ) as client:
                response = await client.get(f"{PDOK_BASE_URL}/free", params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("pdok lookup failed: %s", exc)
            return []
        suggestions = _parse_pdok(payload)
        # "10A" typed → the 10A doc first, the plain 10 and 10B after it (stable otherwise).
        typed = house_number.replace(" ", "").upper()
        return sorted(suggestions, key=lambda s: s.house_number.replace(" ", "").upper() != typed)


def _parse_pdok(payload: Any) -> list[AddressSuggestion]:
    """The ``response.docs`` list, tolerantly: a doc missing its street or city is dropped,
    a malformed payload is an empty answer — the provider contract is "suggestion or silence"."""
    if not isinstance(payload, dict):
        return []
    docs = payload.get("response", {}).get("docs", [])
    if not isinstance(docs, list):
        return []
    suggestions: list[AddressSuggestion] = []
    for doc in docs[:_MAX_SUGGESTIONS]:
        if not isinstance(doc, dict):
            continue
        street = doc.get("straatnaam") or ""
        city = doc.get("woonplaatsnaam") or ""
        if not street or not city:
            continue
        suggestions.append(
            AddressSuggestion(
                street=street,
                house_number=str(doc.get("huis_nlt") or ""),
                postal_code=doc.get("postcode") or "",
                city=city,
                country="NL",
            )
        )
    return suggestions


def get_provider() -> AddressLookupProvider:
    """The instance's provider. PDOK until a keyed provider (Mapbox) ships an org-scoped
    settings row; this is the one seam that choice will hang off."""
    return PdokProvider()
