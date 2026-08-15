"""What the domain register contributes to a client's vital signs (#364).

*Eerstvolgende verlenging.* The date the next conversation about money with this client is
already scheduled for — and the one fact on the strip that is about the *future* rather than the
recent past, which is why it earns a place beside four numbers that all look backwards.

Only domains that actually bill: `invoiceable_condition` is the one resolution the renewal cron, the
list filter and the outstanding picker all take (#298), so a tile and the cron can never disagree
about which domains are ours to renew.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.modules.domains.invoiceable import invoiceable_condition
from app.modules.domains.models import Domain
from app.registry import SummarySpec, SummaryTile

#: Inside this many days a renewal stops being reference and starts being a thing to do.
_SOON = 30


async def _next_renewal(ctx: RequestContext, company_id: uuid.UUID) -> list[SummaryTile]:
    repo = ctx.repo(Domain)
    today = await org_today(ctx.session, ctx.org.id)
    stmt = select(func.min(Domain.next_invoice_date)).where(
        Domain.org_id == ctx.org.id,
        Domain.company_id == company_id,
        Domain.next_invoice_date >= today,
    )
    stmt = stmt.where(invoiceable_condition(ctx.org.id))
    horizon = repo.horizon_condition()
    if horizon is not None:
        stmt = stmt.where(horizon)
    next_date = await ctx.session.scalar(stmt)
    if next_date is None:
        return []
    days = (next_date - today).days
    return [
        SummaryTile(
            key="domains.renewal",
            label_key="companies.summary.next_renewal",
            value=next_date.isoformat(),
            format="date",
            tone="warn" if days <= _SOON else "neutral",
            hint_key="companies.summary.in_days",
            hint_params={"count": max(days, 0)},
            href=f"/domains?company={company_id}",
            position=50,
        )
    ]


domains_company_summary = SummarySpec(
    key="domains.company",
    entity_type="company",
    provider=_next_renewal,
    requires_permission="domains.domain.read",
    position=50,
)
