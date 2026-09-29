"""Meta seam — the Graph transport, the error model and the "which asset is this client's"
protocol, in one place every module that needs Meta may reach.

``meta`` owns the app, the credential and the assets; ``meta_ads`` owns the advertising;
``marketing`` draws a client's numbers. The last may import neither of the first two
(CLAUDE.md §6), so what they share lives here — the shape ``app.core.googleads`` has.

Core owns no Meta *data*. It owns the pipe (:mod:`~app.core.metagraph.client`), the
classification of a refusal (:mod:`~app.core.metagraph.errors`) and the protocol for asking
whoever holds the rows (:mod:`~app.core.metagraph.assets`). A default provider is registered
at import, so the seam always answers and an instance without the integration degrades to a
labelled "not configured" rather than to an import error.
"""

from __future__ import annotations

from app.core.metagraph.assets import (
    KIND_AD_ACCOUNT,
    KIND_INSTAGRAM,
    KIND_PAGE,
    MetaAssetProvider,
    MetaAssetRef,
    meta_assets_for_company,
    meta_call_credentials,
    register_meta_assets,
)
from app.core.metagraph.client import (
    MAX_ITEMS,
    MetaClient,
    MetaCredentials,
    MetaUsage,
    RefreshedToken,
    TokenInfo,
    appsecret_proof,
    debug_token,
    encode_params,
    exchange_token,
    graph_base,
    graph_url,
    meta_client,
    set_transport,
)
from app.core.metagraph.errors import (
    MetaAppSecretError,
    MetaAuthError,
    MetaDuplicate,
    MetaError,
    MetaInvalid,
    MetaNotConfigured,
    MetaNotFound,
    MetaPermissionError,
    MetaPolicyBlock,
    MetaRateLimited,
    MetaUnavailable,
    MetaVersionError,
    classify,
    describe_failure,
    is_retryable,
    scrub,
)

__all__ = [
    "KIND_AD_ACCOUNT",
    "KIND_INSTAGRAM",
    "KIND_PAGE",
    "MAX_ITEMS",
    "MetaAppSecretError",
    "MetaAssetProvider",
    "MetaAssetRef",
    "MetaAuthError",
    "MetaClient",
    "MetaCredentials",
    "MetaDuplicate",
    "MetaError",
    "MetaInvalid",
    "MetaNotConfigured",
    "MetaNotFound",
    "MetaPermissionError",
    "MetaPolicyBlock",
    "MetaRateLimited",
    "MetaUnavailable",
    "MetaUsage",
    "MetaVersionError",
    "RefreshedToken",
    "TokenInfo",
    "appsecret_proof",
    "classify",
    "debug_token",
    "describe_failure",
    "encode_params",
    "exchange_token",
    "graph_base",
    "graph_url",
    "is_retryable",
    "meta_assets_for_company",
    "meta_call_credentials",
    "meta_client",
    "register_meta_assets",
    "scrub",
    "set_transport",
]
