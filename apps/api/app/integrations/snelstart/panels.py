"""What ``snelstart`` shows on a company (epic #377, §6). Business-licensed — see LICENSE.

One panel, and it answers exactly one question: **is this client's bookkeeping in step?** Which
relation they are in SnelStart, how many of their invoices have reached the ledger, and what is
still outstanding *according to SnelStart* rather than according to us.

What it deliberately does **not** do is call SnelStart. It reads stored links, so a company page
loads at full speed and still renders when the administration is unreachable — ``cloudflare``'s
rule, and it matters here because a company page is opened all day and a bookkeeping API is not
on that path.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from app.core.tenancy import RequestContext
from app.integrations.snelstart.models import (
    SnelstartAccount,
    SnelstartLink,
    SnelstartLinkKind,
    SnelstartLinkStatus,
)
from app.registry import PanelSpec


async def company_panel(ctx: RequestContext, company_id: uuid.UUID) -> dict[str, Any]:
    """This client's SnelStart identity and the state of their invoices there.

    Two grouped queries, never one per invoice: a client with sixty invoices renders the same
    number of round trips as one with two (``docs/PERFORMANCE.md``).
    """
    relation = await ctx.session.scalar(
        ctx.repo(SnelstartLink)
        .scoped_select()
        .where(
            SnelstartLink.kind == SnelstartLinkKind.RELATION.value,
            SnelstartLink.local_id == company_id,
        )
        .limit(1)
    )
    counts = {
        str(status): int(total)
        for status, total in await ctx.session.execute(
            select(SnelstartLink.status, func.count())
            .where(
                SnelstartLink.org_id == ctx.org.id,
                SnelstartLink.kind == SnelstartLinkKind.INVOICE.value,
                SnelstartLink.company_id == company_id,
            )
            .group_by(SnelstartLink.status)
        )
    }
    account_name = None
    if relation is not None:
        account_name = await ctx.session.scalar(
            select(SnelstartAccount.administration_name).where(
                SnelstartAccount.id == relation.account_id,
                SnelstartAccount.org_id == ctx.org.id,
            )
        )
    return {
        "linked": relation is not None and relation.status != SnelstartLinkStatus.UNLINKED.value,
        "relation_code": relation.external_code if relation else None,
        "relation_name": relation.external_name if relation else None,
        "relation_status": relation.status if relation else None,
        "administration": account_name,
        "last_synced_at": relation.last_synced_at if relation else None,
        # SnelStart's own untranslatable words for the last failure on this client. Shown on the
        # panel rather than buried in a sync log, because "why is this one client not syncing?"
        # is a question asked from the client's page.
        "last_error": relation.last_error if relation else None,
        "invoices": counts,
        "invoices_pending": counts.get(SnelstartLinkStatus.PENDING.value, 0),
        "invoices_failed": counts.get(SnelstartLinkStatus.ERROR.value, 0),
    }


SNELSTART_PANELS: list[PanelSpec] = [
    PanelSpec(
        key="snelstart.company",
        entity_type="company",
        title_key="snelstart.panel.title",
        provider=company_panel,
        position=95,
        # Whether a client's books are in step is a bookkeeping question, and the people who
        # answer it are the people who may run the sync. Never `invoicing.invoice.read`: #266
        # put a client-portal login behind that key at `:own`, and a client has no business
        # knowing which accounting package their agency uses.
        requires_permission="snelstart.sync.run",
        empty_when=lambda data: not data.get("linked") and not data.get("invoices"),
    )
]
