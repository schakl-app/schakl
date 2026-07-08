"""Core meta endpoints (CLAUDE.md §7): public tenant branding + module discovery.

``/meta/tenant`` is intentionally **unauthenticated** — the login screen needs the tenant's
brand/colours/locale before anyone signs in. It resolves the org from the hostname and reads
its settings through the RLS GUC (no membership required for public branding).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.core.customfields import customizable_entity_types
from app.core.models import OrgSettings
from app.core.tenancy import RequestContext, request_hostname, require_context, resolve_org
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
    enabled_modules: list[str]


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
    # Which modules this org runs; must stay a subset of the instance's mounted modules
    # and always include the hub module (companies).
    enabled_modules: list[str] | None = None


class ModulesMeta(BaseModel):
    enabled_modules: list[str]
    customizable_entity_types: list[str]
    default_locale: str
    supported_locales: list[str]
    local_login_enabled: bool
    oidc_enabled: bool


class MeInfo(BaseModel):
    """The current user *within the resolved tenant* — includes their membership role."""

    id: str
    email: str
    full_name: str | None
    role: str
    can_manage: bool
    locale: str | None


class MeUpdate(BaseModel):
    """Personal, self-service preferences any member may set for their own account."""

    full_name: str | None = None
    locale: str | None = None


def _me_info(ctx: RequestContext, user: User) -> MeInfo:
    return MeInfo(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=ctx.role.value,
        can_manage=ctx.role.can_manage,
        locale=user.locale,
    )


@router.get("/me", response_model=MeInfo)
async def me(ctx: RequestContext = Depends(require_context)) -> MeInfo:
    return _me_info(ctx, ctx.user)


@router.patch("/me", response_model=MeInfo)
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
        setattr(user, key, value)
    await ctx.session.flush()
    await ctx.session.refresh(user)
    return _me_info(ctx, user)


@router.get("/tenant", response_model=TenantBranding)
async def tenant_branding(request: Request) -> TenantBranding:
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
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
            enabled_modules=list(s.enabled_modules)
            if s and s.enabled_modules
            else list(settings.enabled_modules),
        )


@router.patch("/tenant", response_model=TenantBranding)
async def update_tenant_branding(
    payload: TenantBrandingUpdate, ctx: RequestContext = Depends(require_context)
) -> TenantBranding:
    """Update the org's white-label settings (managers only). Applied on next render."""
    ctx.ensure_can_manage()
    data = payload.model_dump(exclude_unset=True)
    if "default_locale" in data and data["default_locale"] not in settings.supported_locales:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"default_locale": "errors.validation"},
        )
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
    for key, value in data.items():
        # Empty strings clear the optional URL fields; required fields ignore empties.
        if key in ("logo_url", "favicon_url"):
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
        enabled_modules=list(s.enabled_modules) if s.enabled_modules else [],
    )


@router.get("/modules", response_model=ModulesMeta)
async def modules() -> ModulesMeta:
    return ModulesMeta(
        enabled_modules=[m.name for m in registry.enabled(settings.enabled_modules)],
        customizable_entity_types=customizable_entity_types(),
        default_locale=settings.default_locale,
        supported_locales=settings.supported_locales,
        local_login_enabled=settings.local_login_enabled,
        oidc_enabled=settings.oidc_enabled,
    )
