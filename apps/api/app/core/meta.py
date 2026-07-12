"""Core meta endpoints (CLAUDE.md §7): public tenant branding + module discovery.

``/meta/tenant`` is intentionally **unauthenticated** — the login screen needs the tenant's
brand/colours/locale before anyone signs in. It resolves the org from the hostname and reads
its settings through the RLS GUC (no membership required for public branding).
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.core.auth import sso
from app.core.auth.models import User
from app.core.currency import DEFAULT_CURRENCY, is_valid_currency
from app.core.customfields import customizable_entity_types
from app.core.entitlements.service import (
    ensure_modules_enableable,
    license_state,
    licensed_skus,
)
from app.core.models import OrgSettings, OrgStatus
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, request_hostname, require_context, resolve_org
from app.core.timezone import is_valid_timezone
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.registry import registry

router = APIRouter(prefix="/meta", tags=["meta"])


class TenantBranding(BaseModel):
    slug: str
    brand_name: str
    show_brand_name: bool
    logo_url: str | None
    favicon_url: str | None
    primary_color: str
    accent_color: str
    default_locale: str
    # IANA zone the tenant's local calendar runs in (CLAUDE.md §8); the web renders event
    # timestamps in it. Public so the login screen and SSR have it before anyone signs in.
    timezone: str
    # ISO 4217 code money renders in (#124) — per-org, like the timezone.
    currency: str
    # Tab-title template (#97): free text with {page} / {brand} tokens; None = built-in format.
    tab_title_template: str | None = None
    enabled_modules: list[str]
    # Suspended orgs still expose branding (the login screen needs it) but every
    # authenticated request is blocked with errors.org_suspended.
    suspended: bool = False


_HEX_COLOR = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"


class TenantBrandingUpdate(BaseModel):
    """White-label settings a manager may change at runtime (CLAUDE.md §7)."""

    brand_name: str | None = Field(default=None, min_length=1, max_length=255)
    show_brand_name: bool | None = None
    logo_url: str | None = Field(default=None, max_length=1024)
    favicon_url: str | None = Field(default=None, max_length=1024)
    primary_color: str | None = Field(default=None, pattern=_HEX_COLOR)
    accent_color: str | None = Field(default=None, pattern=_HEX_COLOR)
    default_locale: str | None = None
    # An IANA zone name; validated against the zoneinfo database in the handler, not by pattern.
    timezone: str | None = Field(default=None, max_length=64)
    # ISO 4217; validated against the known-codes list in the handler.
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    # Tab-title template (#97). Empty string clears it back to the built-in format.
    tab_title_template: str | None = Field(default=None, max_length=120)
    # Which modules this org runs; must stay a subset of the instance's mounted modules
    # and always include the hub module (companies).
    enabled_modules: list[str] | None = None


class ModulesMeta(BaseModel):
    enabled_modules: list[str]
    customizable_entity_types: list[str]
    default_locale: str
    supported_locales: list[str]
    # Per-org, resolved from the hostname at request time (issue #76): false when the resolved
    # org *enforces* OIDC (unless the SCHAKL_FORCE_LOCAL_LOGIN break-glass is set).
    local_login_enabled: bool
    # True iff the resolved org's stored SSO config is enabled **and** complete (issue #6, now
    # per org) — the login page renders its SSO button from this, so it must never say
    # "enabled" while /auth/oidc/login would refuse.
    oidc_enabled: bool
    # The org's display name for the SSO button ("Inloggen met <name>"); set iff oidc_enabled.
    oidc_name: str | None = None
    # Instance config, not a secret (it is in every tenant URL): lets the instance-admin UI
    # compute an org's address as <slug>.<base_domain> when no custom domain is set.
    base_domain: str
    # Licensing (issue #137): module names that require a license, and the subset currently
    # usable — covered by the installed license or inside a grace window (incl. the
    # bootstrap/trial window). Module names only — no license details on an unauthenticated
    # endpoint. The modules settings screen renders its locked/unlocked badges from these.
    licensed_modules: list[str] = Field(default_factory=list)
    entitled_modules: list[str] = Field(default_factory=list)


class MeInfo(BaseModel):
    """The current user *within the resolved tenant* — including what they may do."""

    id: str
    email: str
    full_name: str | None
    #: Effective permissions — the union over every role this membership holds. ``["*"]`` for an
    #: owner. This is UX input, never the boundary: the API is the boundary (issue #19).
    permissions: list[str]
    locale: str | None
    #: Effective avatar (#122): personal override → OIDC picture → None (initials).
    avatar_url: str | None = None
    #: The stored override alone, so Settings → Account can tell it apart from the OIDC one.
    custom_avatar_url: str | None = None
    # Instance administration (issue #26). ``is_instance_admin`` reflects the *effective*
    # user, so it goes false while impersonating; the banner comes from the two fields below.
    is_instance_admin: bool = False
    # Instance owner (users.is_superuser) regardless of SCHAKL_INSTANCE_ADMIN_ENABLED —
    # license management (issue #137) is gated on this alone, so the web needs it even on
    # boxes that keep the cross-tenant admin surface off.
    is_instance_owner: bool = False
    impersonated_by: str | None = None
    impersonation_expires_at: datetime | None = None


class MeUpdate(BaseModel):
    """Personal, self-service preferences any member may set for their own account."""

    full_name: str | None = None
    locale: str | None = None
    #: Personal avatar override (#122): an uploaded file's URL or any image URL; empty clears
    #: it back to the OIDC picture (or initials).
    custom_avatar_url: str | None = Field(default=None, max_length=1024)


def _me_info(ctx: RequestContext, user: User) -> MeInfo:
    return MeInfo(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        permissions=ctx.permissions.keys(),
        locale=user.locale,
        avatar_url=user.custom_avatar_url or user.oidc_avatar_url or None,
        custom_avatar_url=user.custom_avatar_url,
        is_instance_admin=(
            settings.instance_admin_enabled
            and user.is_superuser
            and ctx.impersonated_by is None
        ),
        is_instance_owner=user.is_superuser and ctx.impersonated_by is None,
        impersonated_by=ctx.impersonated_by.email if ctx.impersonated_by else None,
        impersonation_expires_at=ctx.impersonation_expires_at,
    )


@router.get(
    "/me",
    response_model=MeInfo,
    dependencies=[no_permission_required("who am I in this tenant; every member needs it")],
)
async def me(ctx: RequestContext = Depends(require_context)) -> MeInfo:
    return _me_info(ctx, ctx.user)


@router.patch(
    "/me",
    response_model=MeInfo,
    dependencies=[no_permission_required("a user's own name and display language")],
)
async def update_me(
    payload: MeUpdate, ctx: RequestContext = Depends(require_context)
) -> MeInfo:
    """Update the current user's own profile/preferences.

    Not role-gated: even a read-only ``client`` may set their own name and display language.
    """
    data = payload.model_dump(exclude_unset=True)
    if "locale" in data and data["locale"] is not None:
        if data["locale"] not in settings.supported_locales:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"locale": "errors.validation"},
            )
    # ``ctx.user`` comes from the auth session; load a copy on the tenant session to persist.
    user = await ctx.session.get(User, ctx.user.id)
    if user is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    for key, value in data.items():
        # An empty override means "back to the OIDC picture / initials" (#122).
        if key == "custom_avatar_url":
            value = value or None
        setattr(user, key, value)
    await ctx.session.flush()
    await ctx.session.refresh(user)
    return _me_info(ctx, user)


@router.get(
    "/tenant",
    response_model=TenantBranding,
    dependencies=[no_permission_required("public tenant branding; the login screen needs it")],
)
async def tenant_branding(request: Request) -> TenantBranding:
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None:
            raise AppError("unknown_host", "errors.unknown_host", status_code=404)
        await set_current_org(session, org.id)
        s = await session.scalar(select(OrgSettings).where(OrgSettings.org_id == org.id))
        return TenantBranding(
            slug=org.slug,
            brand_name=s.brand_name if s else org.name,
            show_brand_name=s.show_brand_name if s else True,
            logo_url=s.logo_url if s else None,
            favicon_url=s.favicon_url if s else None,
            primary_color=s.primary_color if s else "#4f46e5",
            accent_color=s.accent_color if s else "#0ea5e9",
            default_locale=s.default_locale if s else settings.default_locale,
            timezone=s.timezone if s else settings.default_timezone,
            currency=s.currency if s else DEFAULT_CURRENCY,
            tab_title_template=s.tab_title_template if s else None,
            enabled_modules=list(s.enabled_modules)
            if s and s.enabled_modules
            else list(settings.enabled_modules),
            suspended=org.status == OrgStatus.SUSPENDED.value,
        )


@router.patch(
    "/tenant",
    response_model=TenantBranding,
    dependencies=[require_permission("settings.branding.write")],
)
async def update_tenant_branding(
    payload: TenantBrandingUpdate, ctx: RequestContext = Depends(require_context)
) -> TenantBranding:
    """Update the org's white-label settings. Applied on next render."""
    data = payload.model_dump(exclude_unset=True)
    if "default_locale" in data and data["default_locale"] not in settings.supported_locales:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"default_locale": "errors.validation"},
        )
    if "timezone" in data and not is_valid_timezone(data["timezone"]):
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"timezone": "errors.validation"},
        )
    if "currency" in data and data["currency"] is not None:
        data["currency"] = data["currency"].upper()
        if not is_valid_currency(data["currency"]):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"currency": "errors.validation"},
            )
    if data.get("tab_title_template"):
        template = data["tab_title_template"].strip()
        # Whitelisted tokens only (#97): {page} must appear (otherwise every tab reads the
        # same), and an unknown {token} is a typo, not a variable.
        tokens = re.findall(r"\{([^{}]*)\}", template)
        if "page" not in tokens or any(token not in ("page", "brand") for token in tokens):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"tab_title_template": "errors.validation"},
            )
        data["tab_title_template"] = template
    if data.get("enabled_modules") is not None:
        modules = data["enabled_modules"]
        available = set(settings.enabled_modules)
        if "companies" not in modules or any(m not in available for m in modules):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"enabled_modules": "errors.validation"},
            )

    s = await ctx.session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == ctx.org.id)
    )
    if s is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    if data.get("enabled_modules") is not None:
        # Newly enabling a licensed module requires a covering license (issue #137, 409);
        # keeping an already-enabled one is allowed — the write gate governs that case.
        await ensure_modules_enableable(
            data["enabled_modules"], current=list(s.enabled_modules or [])
        )
    for key, value in data.items():
        # Empty strings clear the optional fields; required fields ignore empties.
        if key in ("logo_url", "favicon_url", "tab_title_template"):
            setattr(s, key, value or None)
        elif isinstance(value, bool) or value:
            setattr(s, key, value)
    await ctx.session.flush()
    await ctx.session.refresh(s)
    return TenantBranding(
        slug=ctx.org.slug,
        brand_name=s.brand_name,
        show_brand_name=s.show_brand_name,
        logo_url=s.logo_url,
        favicon_url=s.favicon_url,
        primary_color=s.primary_color,
        accent_color=s.accent_color,
        default_locale=s.default_locale,
        timezone=s.timezone,
        currency=s.currency,
        tab_title_template=s.tab_title_template,
        enabled_modules=list(s.enabled_modules) if s.enabled_modules else [],
    )


@router.get(
    "/modules",
    response_model=ModulesMeta,
    dependencies=[
        no_permission_required("instance capabilities; the login screen renders from it")
    ],
)
async def modules(request: Request) -> ModulesMeta:
    # The auth flags are tenant data (issue #76): resolve the org from the hostname — the same
    # pre-auth read /meta/tenant does — and reflect its *stored* SSO config at request time.
    # An unknown host gets the defaults; the login page shows its own unknown-host message.
    local_login_enabled = True
    oidc_enabled = False
    oidc_name: str | None = None
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is not None:
            await set_current_org(session, org.id)
            row = await sso.sso_row(session, org.id)
            oidc_enabled = sso.sso_configured(row)
            oidc_name = row.oidc_name if row is not None and oidc_enabled else None
            local_login_enabled = sso.local_login_enabled_for(row)
    state = await license_state()
    module_skus = {
        name: sku for name, sku in licensed_skus().items() if registry.get(name) is not None
    }
    return ModulesMeta(
        enabled_modules=[m.name for m in registry.enabled(settings.enabled_modules)],
        customizable_entity_types=customizable_entity_types(),
        default_locale=settings.default_locale,
        supported_locales=settings.supported_locales,
        local_login_enabled=local_login_enabled,
        oidc_enabled=oidc_enabled,
        oidc_name=oidc_name,
        base_domain=settings.base_domain,
        licensed_modules=sorted(module_skus),
        entitled_modules=sorted(
            name for name, sku in module_skus.items() if state.writable(sku)
        ),
    )
