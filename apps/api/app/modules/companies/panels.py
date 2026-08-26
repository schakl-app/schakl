"""Company-detail panels this module contributes (CLAUDE.md §6, the modular hub).

A panel is a (title + async data provider) the company detail view composes. Future modules
(contacts, websites, hosting, …) attach their own panels to ``entity_type="company"`` the same
way, with no change to the company page.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.companies.models import Company
from app.registry import PROMINENCE_PRIMARY, SIZE_HALF, PanelSpec


async def _details_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    company = await ctx.repo(Company).get_or_404(company_id)
    return {
        "name": company.name,
        "client_number": company.client_number,
        "website": company.website,
        "phone": company.phone,
        "invoice_email": company.invoice_email,
        # Billing identity (issue #11): what invoicing (#207) snapshots at issue. The legal name
        # belongs in this group and nowhere else on the panel — the heading above the card
        # already says "name", and it says the label, which is the one a colleague is looking
        # for. Sent raw (``None`` when the label is also the legal name) rather than resolved,
        # so the screen can draw the difference instead of a line that silently repeats the H1.
        "legal_name": company.legal_name,
        "vat_number": company.vat_number,
        "coc_number": company.coc_number,
        "address_line1": company.address_line1,
        "house_number": company.house_number,
        "address_line2": company.address_line2,
        "postal_code": company.postal_code,
        "city": company.city,
        "country": company.country,
        "notes": company.notes,
        "custom": company.custom,
    }


company_details_panel = PanelSpec(
    key="companies.details",
    entity_type="company",
    title_key="companies.panel.details",
    provider=_details_provider,
    position=45,
    # Whoever may open the client may read its own definition — this is the record the page
    # *is*. Declared rather than omitted (#365): the hub's own panel is the one every later
    # module copies, so it has to show the shape.
    requires_permission="companies.company.read",
    # A working surface, at the *end* of the working lane. #364 filed this under VASTGELEGD and
    # buried it 1.100 px down; #403 pulled it to the top; the owner then asked for the middle
    # position: the vital signs and the live work (contacts, projects, taken, contactmomenten,
    # uren) come first, and the address block closes the lane — consulted when the phone rings,
    # but not the reason the page is opened. Still primary: a register heading over the record's
    # own definition read as filing it away.
    #
    # Half width — an address and six labelled values do not want 1150 px — and since #403 that
    # is what it is drawn at, whether or not it has a neighbour.
    prominence=PROMINENCE_PRIMARY,
    size=SIZE_HALF,
)
