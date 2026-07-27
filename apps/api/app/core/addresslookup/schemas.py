"""Response shapes for the address lookup endpoint (#241)."""

from __future__ import annotations

from pydantic import BaseModel


class AddressSuggestionOut(BaseModel):
    street: str
    house_number: str
    postal_code: str
    city: str
    #: ISO 3166-1 alpha-2, matching ``Company.country``.
    country: str


class AddressLookupResponse(BaseModel):
    suggestions: list[AddressSuggestionOut]
