"""Core meta endpoints (CLAUDE.md §7): public tenant branding + module discovery.

``/meta/tenant`` is intentionally **unauthenticated** — the login screen needs the tenant's
brand/colours/locale before anyone signs in. It resolves the org from the hostname and reads
its settings through the RLS GUC (no membership required for public branding).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.core.customfields import customizable_entity_types
from app.core.models import OrgSettings
from app.core.tenancy import request_hostname, resolve_org
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.registry import registry

router = APIRouter(prefix="/meta", tags=["meta"])


class TenantBranding(BaseModel):
    slug: str
    brand_name: str
    logo_url: str | None
    favicon_url: str | None
    primary_color: str
    accent_color: str
    default_locale: str
    enabled_modules: list[str]


class ModulesMeta(BaseModel):
    enabled_modules: list[str]
    customizable_entity_types: list[str]
    default_locale: str
    supported_locales: list[str]
    local_login_enabled: bool
    oidc_enabled: bool


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
            logo_url=s.logo_url if s else None,
            favicon_url=s.favicon_url if s else None,
            primary_color=s.primary_color if s else "#4f46e5",
            accent_color=s.accent_color if s else "#0ea5e9",
            default_locale=s.default_locale if s else settings.default_locale,
            enabled_modules=list(s.enabled_modules)
            if s and s.enabled_modules
            else list(settings.enabled_modules),
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
