"""Google Ads seam (epic: the ``google_ads`` module) — the transport, the error model and the
"which account is this client's" protocol, in one place both modules that need it may reach.

``google_ads`` owns the accounts, the depth and the writes; ``marketing`` draws a spend tile on a
client's dashboard from the same API. Neither may import the other (CLAUDE.md §6), so what they
share lives here — the shape ``app.core.registrar`` already uses for registrars and
``app.core.payments`` for payment providers.

Core owns no Ads *data*. It owns the pipe (:mod:`~app.core.googleads.client`), the classification
of a refusal (:mod:`~app.core.googleads.errors`), the guard on the query passthrough
(:mod:`~app.core.googleads.gaql`) and the protocol for asking whoever holds the rows
(:mod:`~app.core.googleads.accounts`). A default provider is registered here at import, so the
seam always answers and an instance running without the module degrades to a labelled
"not configured" rather than an import error or a 500.
"""

from __future__ import annotations

from app.core.googleads.accounts import (
    AdsAccountProvider,
    AdsAccountRef,
    AdsCallParams,
    ads_accounts_for_company,
    ads_accounts_registered,
    ads_call_params,
    ads_developer_token,
    attach_ads_account,
    register_ads_accounts,
)
from app.core.googleads.client import (
    DEFAULT_API_VERSION,
    MAX_PAGES,
    MAX_ROWS,
    AdsClient,
    AdsCredentials,
    QueryStats,
    ads_client,
    api_base,
    format_customer_id,
    normalise_customer_id,
    set_transport,
)
from app.core.googleads.errors import (
    AdsAuthError,
    AdsDeveloperTokenError,
    AdsError,
    AdsNotConfigured,
    AdsPermissionError,
    AdsQueryError,
    AdsQuotaError,
    AdsUnavailable,
    AdsVersionError,
    OperationFailure,
    classify,
    describe_failure,
    partial_failures,
    scrub,
)

__all__ = [
    "DEFAULT_API_VERSION",
    "MAX_PAGES",
    "MAX_ROWS",
    "AdsAccountProvider",
    "AdsAccountRef",
    "AdsAuthError",
    "AdsCallParams",
    "AdsClient",
    "AdsCredentials",
    "AdsDeveloperTokenError",
    "AdsError",
    "AdsNotConfigured",
    "AdsPermissionError",
    "AdsQueryError",
    "AdsQuotaError",
    "AdsUnavailable",
    "AdsVersionError",
    "OperationFailure",
    "QueryStats",
    "ads_accounts_for_company",
    "ads_accounts_registered",
    "ads_call_params",
    "ads_client",
    "ads_developer_token",
    "api_base",
    "attach_ads_account",
    "classify",
    "describe_failure",
    "format_customer_id",
    "normalise_customer_id",
    "partial_failures",
    "register_ads_accounts",
    "scrub",
    "set_transport",
]
