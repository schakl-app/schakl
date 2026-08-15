"""The Google Ads panel on a client's page. Business-licensed — see LICENSE.

**It reads stored rows and calls Google not at all.** A company page composes every enabled
module's panel in sequence with no per-panel try/except, so one slow or refusing integration
would hold — or break — the whole hub. What it shows is what schakl already knows: which Ads
accounts this client has, whether each still answers, and a link to the numbers. The numbers
themselves are one click away, where waiting for Google is the point rather than a surprise.
"""

from __future__ import annotations

import uuid

from app.core.googleads import format_customer_id
from app.core.tenancy import RequestContext
from app.modules.google_ads.service import GoogleAdsService
from app.registry import PanelSpec


async def _provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    if not ctx.can("google_ads.account.read"):
        # Quiet rather than an error: the panel is permission-gated, and a card reading "no
        # access" on a page full of working cards teaches nobody anything.
        return {"forbidden": True}
    accounts = await GoogleAdsService(ctx).list_accounts(company_id=company_id, active_only=True)
    return {
        "accounts": [
            {
                "id": str(row.id),
                "customer_id": format_customer_id(row.customer_id),
                "name": row.descriptive_name,
                "currency": row.currency_code,
                "status": row.status,
                # Google's own sentence, already scrubbed. Shown as-is: it is the one thing that
                # says *what* to fix, and translating it would mean inventing categories Google
                # does not have.
                "last_error": row.last_error,
                "last_verified_at": (
                    row.last_verified_at.isoformat() if row.last_verified_at else None
                ),
            }
            for row in accounts
        ],
        "can_manage": ctx.can("google_ads.settings.manage"),
        # The connect control on this panel posts a **marketing link** (#338), so it mirrors
        # that key and not this module's — #310's rule: a control gated on the permission the
        # screen is *about* rather than the one its call makes renders for someone the API
        # then refuses, with a bare "no access" no label on the screen explains.
        "can_link": ctx.can("marketing.link.manage"),
    }


google_ads_company_panel = PanelSpec(
    key="google_ads.company",
    entity_type="company",
    title_key="google_ads.panel.title",
    provider=_provider,
    position=51,
)
