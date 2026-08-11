"""REST endpoints for google_ads under ``/api/v1/google-ads``. Business-licensed — see LICENSE.

Deny-by-default: every route declares one of the eight ``google_ads.*`` permissions (§15). No
route here carries ``no_permission_required`` and none should — ``test_rbac_deny_by_default``'s
exemption list has no google-ads entry, and needing one would mean something on this surface is
readable without a grant.

**These routes are the MCP surface.** Every ``/api/v1`` operation becomes an MCP tool, generated
from this app's own OpenAPI document and proxied in-process back through ``require_context``
(CLAUDE.md §12) — so the tool name is the handler's name shortened at ``_api_v1_``, and the
permission a key must carry is the one declared right here. That is why the handlers are named
for what an agent would ask for (``list_google_ads_accounts``, not ``list_accounts``, which
would collide with cloudflare's and fall back to the unreadable full operation id).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.googleads import format_customer_id
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.google_ads.models import GoogleAdsAccount
from app.modules.google_ads.schemas import (
    GoogleAdsAccountCreate,
    GoogleAdsAccountRead,
    GoogleAdsAccountUpdate,
    GoogleAdsAvailableAccount,
    GoogleAdsPickerRead,
    GoogleAdsSettingsRead,
    GoogleAdsSettingsWrite,
)
from app.modules.google_ads.service import GoogleAdsService

router = APIRouter(prefix="/google-ads", tags=["google_ads"])


def _read(row: GoogleAdsAccount, company_name: str | None = None) -> GoogleAdsAccountRead:
    return GoogleAdsAccountRead(
        **{
            **{c.name: getattr(row, c.name) for c in row.__table__.columns},
            "customer_id_formatted": format_customer_id(row.customer_id),
            "company_name": company_name,
        }
    )


# --- settings (the credential — the highest-blast-radius surface here) ------------------- #
@router.get(
    "/settings",
    response_model=GoogleAdsSettingsRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def get_google_ads_settings(
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsSettingsRead:
    """The org's Ads configuration. The developer token is never part of the response."""
    from app.config import settings as app_settings

    service = GoogleAdsService(ctx)
    row = await service.settings_row()
    return GoogleAdsSettingsRead(
        developer_token_configured=bool(row and row.developer_token_encrypted),
        env_token_configured=bool((app_settings.google_ads_developer_token or "").strip()),
        default_login_customer_id=row.default_login_customer_id if row else None,
        writes_enabled=row.writes_enabled if row else True,
    )


@router.put(
    "/settings",
    response_model=GoogleAdsSettingsRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def save_google_ads_settings(
    payload: GoogleAdsSettingsWrite, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsSettingsRead:
    await GoogleAdsService(ctx).save_settings(
        developer_token=payload.developer_token,
        default_login_customer_id=payload.default_login_customer_id,
        writes_enabled=payload.writes_enabled,
    )
    return await get_google_ads_settings(ctx)


# --- accounts ---------------------------------------------------------------------------- #
@router.get(
    "/accounts",
    response_model=list[GoogleAdsAccountRead],
    dependencies=[require_permission("google_ads.account.read")],
)
async def list_google_ads_accounts(
    company_id: uuid.UUID | None = Query(default=None),
    active_only: bool = Query(default=False),
    ctx: RequestContext = Depends(require_context),
) -> list[GoogleAdsAccountRead]:
    """Every linked Google Ads account this caller may see — **start here**.

    The list an agent needs before anything else: it names the accounts, and every other tool
    takes one of these ids. Company-scoped logins see only the accounts of the clients in their
    horizon, and an account attached to no client (the agency's own) stays visible to all.
    """
    rows = await GoogleAdsService(ctx).list_accounts(company_id=company_id, active_only=active_only)
    return [_read(row) for row in rows]


@router.get(
    "/accounts/available",
    response_model=GoogleAdsPickerRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def list_available_google_ads_accounts(
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsPickerRead:
    """Accounts the caller's own Google grant can reach, manager hierarchies expanded.

    Live — this is the one read that calls Google on every request, because a picker showing a
    stale account list is how someone links an account that was closed last month.
    """
    result = await GoogleAdsService(ctx).available_accounts()
    return GoogleAdsPickerRead(
        accounts=[
            GoogleAdsAvailableAccount(
                **{
                    **option.__dict__,
                    "customer_id_formatted": format_customer_id(option.customer_id),
                }
            )
            for option in result.accounts
        ],
        warnings=list(result.warnings),
    )


@router.get(
    "/accounts/{account_id}",
    response_model=GoogleAdsAccountRead,
    dependencies=[require_permission("google_ads.account.read")],
)
async def get_google_ads_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsAccountRead:
    return _read(await GoogleAdsService(ctx).get_account(account_id))


@router.post(
    "/accounts",
    response_model=GoogleAdsAccountRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def link_google_ads_account(
    payload: GoogleAdsAccountCreate, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsAccountRead:
    """Link an Ads account to a client.

    The linking user's Google connection is stamped on the row: it is the grant that will sync
    it, and an account whose connection is later removed goes dormant and asks to be
    reconnected rather than silently syncing as somebody else.
    """
    from app.modules.google import client as google_client

    connection = await google_client.connection_for(ctx.session, ctx.org.id, ctx.user.id)
    row = await GoogleAdsService(ctx).attach(
        customer_id=payload.customer_id,
        company_id=payload.company_id,
        login_customer_id=payload.login_customer_id,
        connection_id=connection.id if connection else None,
        descriptive_name=payload.descriptive_name,
        currency_code=payload.currency_code,
    )
    return _read(row)


@router.patch(
    "/accounts/{account_id}",
    response_model=GoogleAdsAccountRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def update_google_ads_account(
    account_id: uuid.UUID,
    payload: GoogleAdsAccountUpdate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsAccountRead:
    service = GoogleAdsService(ctx)
    row = await service.get_account(account_id)
    await service.update_account(
        row,
        company_id=payload.company_id,
        login_customer_id=payload.login_customer_id,
        active=payload.active,
        company_id_set="company_id" in payload.model_fields_set,
    )
    return _read(row)


@router.delete(
    "/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def unlink_google_ads_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> Response:
    """Deactivate the link. The row survives: history hangs off it, and a re-link must find the
    same account rather than collide with its own unique constraint."""
    await GoogleAdsService(ctx).unlink(account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/accounts/{account_id}/verify",
    response_model=GoogleAdsAccountRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def verify_google_ads_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsAccountRead:
    """Ask Google what it says about this account, and record the answer either way.

    Never raises for a refusal: the outcome *is* the payload (``status``, ``last_error``), which
    is what lets a screen say "the grant was revoked" instead of showing a red toast with no
    detail. A success clears the flag it may have set last time.
    """
    return _read(await GoogleAdsService(ctx).verify(account_id))
