"""The Meta Ads panel on the company hub (§6): the ad accounts managed for this client.

A register, read from our own rows. What an account *spends* is a live read against Meta,
and a hub composes a dozen panels on one page load: the figures are one click away, on the
account's own page, where they are asked for once and by somebody who wants them.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.metagraph.assets import KIND_AD_ACCOUNT
from app.core.tenancy import RequestContext
from app.integrations.meta.models import MetaAsset
from app.registry import PROMINENCE_REGISTER, SIZE_HALF, PanelSpec


async def _accounts_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict[str, Any]:
    rows = (
        await ctx.session.scalars(
            ctx.repo(MetaAsset)
            .scoped_select()
            .where(
                MetaAsset.company_id == company_id,
                MetaAsset.active.is_(True),
                MetaAsset.kind == KIND_AD_ACCOUNT,
            )
            .order_by(MetaAsset.name)
            .limit(20)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "name": row.name,
                "meta_id": row.external_id,
                "currency": row.currency,
                "status": row.status,
            }
            for row in rows
        ]
    }


meta_ads_company_panel = PanelSpec(
    key="meta_ads.company",
    entity_type="company",
    title_key="meta_ads.panel.title",
    provider=_accounts_provider,
    # Directly under the social panel.
    position=47,
    requires_permission="meta_ads.account.read",
    prominence=PROMINENCE_REGISTER,
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("items"),
)
