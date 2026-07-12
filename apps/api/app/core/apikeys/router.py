"""REST endpoints for API keys and service accounts (#20).

Personal keys live under ``/api/v1/api-keys`` (Settings → Account); service accounts and their
keys under ``/api/v1/service-accounts`` (Instellingen). The full secret is returned exactly
once, at creation; every other response is redacted.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.core.apikeys import keys as keygen
from app.core.apikeys.models import ApiKey
from app.core.apikeys.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    ServiceAccountCreate,
    ServiceAccountKeyCreate,
    ServiceAccountRead,
)
from app.core.apikeys.service import ApiKeyService
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(tags=["api-keys"])


def _key_read(key: ApiKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=key.id,
        org_id=key.org_id,
        name=key.name,
        redacted=keygen.redacted(key.prefix),
        principal_type=key.principal_type,
        user_id=key.user_id,
        service_account_id=key.service_account_id,
        scopes=list(key.scopes),
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        created_at=key.created_at,
    )


# --- personal keys (Settings → Account) --------------------------------------- #
@router.get(
    "/api-keys",
    response_model=list[ApiKeyRead],
    dependencies=[require_permission("apikeys.personal.manage")],
)
async def list_personal_keys(
    ctx: RequestContext = Depends(require_context),
) -> list[ApiKeyRead]:
    return [_key_read(k) for k in await ApiKeyService(ctx).list_personal()]


@router.post(
    "/api-keys",
    response_model=ApiKeyCreated,
    status_code=201,
    dependencies=[require_permission("apikeys.personal.manage")],
)
async def create_personal_key(
    payload: ApiKeyCreate,
    ctx: RequestContext = Depends(require_context),
) -> ApiKeyCreated:
    key, secret = await ApiKeyService(ctx).create_personal(payload)
    return ApiKeyCreated(**_key_read(key).model_dump(), secret=secret)


@router.post(
    "/api-keys/{key_id}/revoke",
    response_model=ApiKeyRead,
    dependencies=[require_permission("apikeys.personal.manage")],
)
async def revoke_key(
    key_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> ApiKeyRead:
    """Revoke a key. Own personal keys need ``apikeys.personal.manage``; the service refines to
    ``apikeys.service_account.manage`` for a service-account key."""
    return _key_read(await ApiKeyService(ctx).revoke(key_id))


# --- service accounts (Instellingen) ------------------------------------------ #
@router.get(
    "/service-accounts",
    response_model=list[ServiceAccountRead],
    dependencies=[require_permission("apikeys.service_account.manage")],
)
async def list_service_accounts(
    ctx: RequestContext = Depends(require_context),
) -> list[ServiceAccountRead]:
    return [ServiceAccountRead.model_validate(a) for a in await ApiKeyService(ctx).list_accounts()]


@router.post(
    "/service-accounts",
    response_model=ServiceAccountRead,
    status_code=201,
    dependencies=[require_permission("apikeys.service_account.manage")],
)
async def create_service_account(
    payload: ServiceAccountCreate,
    ctx: RequestContext = Depends(require_context),
) -> ServiceAccountRead:
    return ServiceAccountRead.model_validate(await ApiKeyService(ctx).create_account(payload))


@router.delete(
    "/service-accounts/{account_id}",
    status_code=204,
    dependencies=[require_permission("apikeys.service_account.manage")],
)
async def delete_service_account(
    account_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ApiKeyService(ctx).delete_account(account_id)


@router.get(
    "/service-accounts/{account_id}/keys",
    response_model=list[ApiKeyRead],
    dependencies=[require_permission("apikeys.service_account.manage")],
)
async def list_service_account_keys(
    account_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[ApiKeyRead]:
    return [_key_read(k) for k in await ApiKeyService(ctx).list_for_account(account_id)]


@router.post(
    "/service-accounts/{account_id}/keys",
    response_model=ApiKeyCreated,
    status_code=201,
    dependencies=[require_permission("apikeys.service_account.manage")],
)
async def create_service_account_key(
    account_id: uuid.UUID,
    payload: ApiKeyCreate,
    ctx: RequestContext = Depends(require_context),
) -> ApiKeyCreated:
    data = ServiceAccountKeyCreate(**payload.model_dump(), service_account_id=account_id)
    key, secret = await ApiKeyService(ctx).create_for_account(data)
    return ApiKeyCreated(**_key_read(key).model_dump(), secret=secret)
