"""Domains panel on the company detail view (issue #90, the modular hub — CLAUDE.md §6).

Lists a client's domains (name, status, whether email is on) so the company page composes them
via the registry with no edits to that page. Registered against ``entity_type="company"``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import bindparam, text

from app.core.tenancy import RequestContext
from app.modules.domains.service import DomainService
from app.registry import PanelSpec

#: How many domains the client card shows before handing over to the register.
#:
#: The panel is deliberately the **first page of the list it links to**: same filter, same
#: default sort, so "Alle 23 bekijken" opens on the five that were already on screen followed by
#: the rest, rather than on a differently-ordered set the user has to re-find their place in.
#: That is also why this reads through ``list`` rather than a bespoke query — a client with a
#: 400-name portfolio used to load every one of them to render a handful.
_PANEL_LIMIT = 5


async def _domains_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    domains, total = await DomainService(ctx).list(
        limit=_PANEL_LIMIT, offset=0, company_id=company_id
    )
    # Which domains already carry their (0/1) website — so the panel can link to it, or offer
    # "＋ website" where there is none: everything for a client starts from the client's page.
    # Raw table SQL (the websites service's own `_attach` pattern) — never a Python import of
    # another module's internals.
    website_by_domain: dict[uuid.UUID, uuid.UUID] = {}
    if domains:
        rows = (
            await ctx.session.execute(
                text(
                    "SELECT domain_id, id FROM websites"
                    " WHERE org_id = :org_id AND domain_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"org_id": ctx.org.id, "ids": [d.id for d in domains]},
            )
        ).all()
        website_by_domain = {row[0]: row[1] for row in rows}
    return {
        # The whole count, not the shown one: a card that says "5" over a client who has 23 is
        # the truncated-total failure (#37) in miniature — it reads as the complete answer.
        "total": total,
        "domains": [
            {
                "id": str(d.id),
                "name": d.name,
                "status": d.status,
                "email_enabled": d.email_enabled,
                "has_website": d.id in website_by_domain,
                # Renewal + resolved price (#250): what this domain costs and when it next
                # bills — the numbers the client conversation is about.
                "next_invoice_date": (
                    d.next_invoice_date.isoformat() if d.next_invoice_date else None
                ),
                "resolved_price": (
                    str(d.resolved_price) if d.resolved_price is not None else None  # type: ignore[attr-defined]
                ),
                "resolved_currency": d.resolved_currency,  # type: ignore[attr-defined]
            }
            for d in domains
        ],
    }


domains_company_panel = PanelSpec(
    key="domains.company",
    entity_type="company",
    title_key="domains.panel.title",
    provider=_domains_provider,
    # Rarely-consulted asset panel: near the bottom, after websites, before only the trail.
    position=75,
)
