"""``GET /api/v1/addresslookup`` — postcode + house number → address suggestions (#241).

Gated by the core ``addresslookup.lookup`` permission (staff by default: anyone who may fill
in an address form; a portal client edits no addresses). The route reads no tenant rows —
``require_context`` still runs so the call is authenticated, org-bound and rate-limited like
every other, and the response is only ever public registry data. Fail-soft is the provider's
contract: a down or slow provider answers ``{"suggestions": []}``, never a 5xx.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.addresslookup.providers import get_provider
from app.core.addresslookup.schemas import AddressLookupResponse, AddressSuggestionOut
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/addresslookup", tags=["addresslookup"])


@router.get(
    "",
    response_model=AddressLookupResponse,
    dependencies=[require_permission("addresslookup.lookup")],
)
async def lookup_address(
    postal_code: str = Query(..., min_length=1, max_length=16),
    house_number: str = Query(..., min_length=1, max_length=16),
    ctx: RequestContext = Depends(require_context),
) -> AddressLookupResponse:
    suggestions = await get_provider().lookup(postal_code, house_number)
    return AddressLookupResponse(
        suggestions=[
            AddressSuggestionOut(
                street=s.street,
                house_number=s.house_number,
                postal_code=s.postal_code,
                city=s.city,
                country=s.country,
            )
            for s in suggestions
        ]
    )
