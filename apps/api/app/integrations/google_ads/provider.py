"""This module's answer to ``app.core.googleads.accounts``. Business-licensed — see LICENSE.

Thin on purpose. The seam's whole value is that a borrower — today ``marketing``, tomorrow
whatever draws an Ads number on a dashboard — asks one question and never learns this module's
tables, its service or its permissions. So this file translates and does nothing else.

**It deliberately does not re-check permissions.** The borrower is already inside its own gated
route, and the two rules that actually protect the rows travel with the query: RLS binds the org,
and every read here goes through ``ctx.repo(...).scoped_select()``, which carries the company
horizon. Adding ``google_ads.account.read`` on top would mean a marketing dashboard silently
losing its spend tile for every member the agency had not thought to grant a *second* module's
permission to — a refusal with no control anywhere on screen to explain it.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.googleads import AdsAccountRef, AdsCallParams, register_ads_accounts
from app.integrations.google_ads.service import GoogleAdsService


class GoogleAdsAccountProvider:
    async def call_params(
        self,
        ctx: Any,
        *,
        account_id: uuid.UUID | None = None,
        customer_id: str | None = None,
    ) -> AdsCallParams:
        return await GoogleAdsService(ctx).call_params(
            account_id=account_id, customer_id=customer_id
        )

    async def accounts_for_company(self, ctx: Any, company_id: uuid.UUID) -> list[AdsAccountRef]:
        return await GoogleAdsService(ctx).accounts_for_company(company_id)

    async def attach(
        self,
        ctx: Any,
        *,
        customer_id: str,
        company_id: uuid.UUID | None = None,
        login_customer_id: str | None = None,
        connection_id: uuid.UUID | None = None,
        descriptive_name: str = "",
        currency_code: str | None = None,
    ) -> AdsAccountRef:
        from app.integrations.google_ads.service import _ref

        row = await GoogleAdsService(ctx).attach(
            customer_id=customer_id,
            company_id=company_id,
            login_customer_id=login_customer_id,
            connection_id=connection_id,
            descriptive_name=descriptive_name,
            currency_code=currency_code,
        )
        return _ref(row)

    async def developer_token(self, ctx: Any) -> str:
        return await GoogleAdsService(ctx).developer_token()


def install() -> None:
    register_ads_accounts(GoogleAdsAccountProvider())
