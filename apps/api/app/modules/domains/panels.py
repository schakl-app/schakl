"""Domains panel on the company detail view (issue #90, the modular hub — CLAUDE.md §6).

Lists a client's domains (name, status, whether email is on) so the company page composes them
via the registry with no edits to that page. Registered against ``entity_type="company"``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import bindparam, text

from app.core.tenancy import RequestContext
from app.core.webaddress import website_label_sql
from app.modules.domains.service import DomainService
from app.registry import SIZE_HALF, PanelSpec

#: How many domains the client card shows before handing over to the register.
#:
#: The panel is deliberately the **first page of the list it links to**: same filter, same
#: default sort, so "Alle 23 bekijken" opens on the five that were already on screen followed by
#: the rest, rather than on a differently-ordered set the user has to re-find their place in.
#: That is also why this reads through ``list`` rather than a bespoke query — a client with a
#: 400-name portfolio used to load every one of them to render a handful.
_PANEL_LIMIT = 5


async def _domains_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    service = DomainService(ctx)
    domains, total = await service.list(limit=_PANEL_LIMIT, offset=0, company_id=company_id)
    # What the whole portfolio comes to — the five rows are a window, the figure is the answer.
    # Invoiced and not-invoiced kept apart (#298): an agency's own names are set *not invoiced*
    # and still cost their renewal. One grouped statement, skipped when there is nothing to add.
    totals = (await service.totals(company_id=company_id)).total if total else None
    # Which domains already carry websites — so the panel can link to them, or offer
    # "＋ website" where there is none: everything for a client starts from the client's page.
    # A domain may carry several (one per address, app/core/webaddress.py), so the row gets
    # the list. Raw table SQL (the websites service's own `_attach` pattern) — never a Python
    # import of another module's internals.
    websites_by_domain: dict[uuid.UUID, list[dict[str, str]]] = {}
    if domains:
        rows = (
            await ctx.session.execute(
                text(
                    f"SELECT w.domain_id, w.id, {website_label_sql()} FROM websites w"
                    " JOIN domains d ON d.id = w.domain_id"
                    " WHERE w.org_id = :org_id AND w.domain_id IN :ids"
                    " ORDER BY w.root DESC, w.path"
                ).bindparams(bindparam("ids", expanding=True)),
                {"org_id": ctx.org.id, "ids": [d.id for d in domains]},
            )
        ).all()
        for domain_id, website_id, label in rows:
            websites_by_domain.setdefault(domain_id, []).append(
                {"id": str(website_id), "label": label}
            )
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
                # The website's **id**, not just whether there is one: it was already resolved
                # here (the map below is keyed by domain and valued by website), and a website
                # has had its own detail page since it stopped sharing the domain's. Emitting
                # the boolean meant the panel could only link at the domain and let the reader
                # find the site from there.
                "website_id": (
                    websites_by_domain[d.id][0]["id"] if d.id in websites_by_domain else None
                ),
                # Every site on the domain, by address — the id above is the first of
                # these, kept for the older panel that links to exactly one.
                "websites": websites_by_domain.get(d.id, []),
                # Renewal + resolved price (#250): what this domain costs and when it next
                # bills — the numbers the client conversation is about.
                "next_invoice_date": (
                    d.next_invoice_date.isoformat() if d.next_invoice_date else None
                ),
                "resolved_price": (
                    str(d.resolved_price) if d.resolved_price is not None else None  # type: ignore[attr-defined]
                ),
                "resolved_currency": d.resolved_currency,  # type: ignore[attr-defined]
                # Resolved, not stored (#298): a price on a row nobody bills reads as revenue
                # unless the row says otherwise.
                "invoiceable": d.invoiceable_effective,  # type: ignore[attr-defined]
            }
            for d in domains
        ],
        "totals": totals.model_dump(mode="json") if totals is not None else None,
    }


domains_company_panel = PanelSpec(
    key="domains.company",
    entity_type="company",
    title_key="domains.panel.title",
    provider=_domains_provider,
    # `resolved_price` per domain went to anyone who could open the client (#365).
    requires_permission="domains.domain.read",
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("domains"),
    # Rarely-consulted asset panel: near the bottom, after websites, before only the trail.
    position=75,
)
