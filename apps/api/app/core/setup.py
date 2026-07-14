"""First-run setup (issue #26): the wizard that replaces the seed script.

A fresh install has **zero orgs**; until the first org exists these endpoints are open (there
is nobody to authenticate yet) and the web app routes every visit to the wizard. One POST
creates the org, its settings, and the owner — who is also the **instance owner**
(``is_superuser``): whoever installs the box operates it. The moment an org exists the
surface closes for good (guarded by an advisory lock against concurrent first requests).

The hostname the wizard is reached on is claimed as the org's verified custom domain (unless
it is already ``<slug>.<base_domain>``): resolution no longer falls back to "the only org",
so the install must leave the wizard reachable at the address it was set up on. Auto-verify
is safe *only* here — the box has no tenants yet, so there is nobody to phish.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from app.config import settings
from app.core.auth.models import User
from app.core.instance import audit, repo
from app.core.instance import service as org_service
from app.core.models import Org, OrgSettings
from app.core.permissions.catalog import ROLE_OWNER
from app.core.permissions.deps import no_permission_required
from app.core.permissions.service import create_membership, seed_system_roles
from app.core.tenancy import request_hostname
from app.core.timezone import is_valid_timezone
from app.db import async_session_maker, set_current_org
from app.errors import AppError

router = APIRouter(
    prefix="/setup",
    tags=["setup"],
    # There is no org, no user and no membership yet — there is nothing to have a permission in.
    dependencies=[no_permission_required("first-run wizard: runs before any org exists")],
)

_password_hash = PasswordHash.recommended()
_HEX_COLOR = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=63)
    brand_name: str | None = Field(default=None, max_length=255)
    primary_color: str | None = Field(default=None, pattern=_HEX_COLOR)
    accent_color: str | None = Field(default=None, pattern=_HEX_COLOR)
    locale: str | None = None
    timezone: str | None = Field(default=None, max_length=64)
    enabled_modules: list[str] | None = None
    owner_email: EmailStr
    owner_password: str = Field(min_length=8, max_length=128)
    owner_full_name: str | None = Field(default=None, max_length=255)


class SetupResult(BaseModel):
    slug: str
    host: str | None


@router.get("/status", response_model=SetupStatus)
async def setup_status() -> SetupStatus:
    # In demo mode the seeder owns org creation (#141) — the wizard must never run, so it must
    # never present itself as needed either.
    if settings.demo_mode:
        return SetupStatus(needs_setup=False)
    async with async_session_maker() as session:
        return SetupStatus(needs_setup=await repo.org_count(session) == 0)


@router.post("", response_model=SetupResult, status_code=201)
async def run_setup(payload: SetupRequest, request: Request) -> SetupResult:
    if settings.demo_mode:
        # The demo seeder creates the one org; a visitor must not run the first-run wizard (#141).
        raise AppError("demo_blocked", "errors.demo_blocked", status_code=403)
    slug = org_service.validate_slug(payload.slug)
    locale = payload.locale or settings.default_locale
    if locale not in settings.supported_locales:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"locale": "errors.validation"},
        )
    timezone = payload.timezone or settings.default_timezone
    if not is_valid_timezone(timezone):
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"timezone": "errors.validation"},
        )
    modules = org_service.validate_modules(
        payload.enabled_modules
        if payload.enabled_modules is not None
        else list(settings.enabled_modules)
    )

    async with async_session_maker() as session:
        # Two racing first requests must not both pass the zero-orgs check.
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('schakl_setup'))"))
        if await repo.org_count(session) > 0:
            raise AppError("setup_already_done", "errors.setup_already_done", status_code=409)

        org = Org(slug=slug, name=payload.org_name)
        host = request_hostname(request)
        if host and host != f"{slug}.{settings.base_domain.lower()}":
            org.custom_domain = host
            org.custom_domain_verified_at = datetime.now(UTC)
        session.add(org)
        await session.flush()

        owner = User(
            email=payload.owner_email.lower(),
            hashed_password=_password_hash.hash(payload.owner_password),
            full_name=payload.owner_full_name,
            is_active=True,
            is_verified=True,
            # The installer operates the box: instance owner, not just org owner.
            is_superuser=True,
        )
        session.add(owner)
        await session.flush()

        await set_current_org(session, org.id)
        session.add(
            OrgSettings(
                org_id=org.id,
                brand_name=payload.brand_name or payload.org_name,
                primary_color=payload.primary_color or "#4f46e5",
                accent_color=payload.accent_color or "#0ea5e9",
                default_locale=locale,
                timezone=timezone,
                enabled_modules=modules,
            )
        )
        await session.flush()
        # The four system roles must exist before the first membership can hold one (issue #19),
        # and ``seed_system_roles`` stamps ``org_settings.applied_permission_defaults``.
        await seed_system_roles(session, org.id)
        await create_membership(session, org.id, owner.id, ROLE_OWNER)
        await session.flush()
        await audit.record(
            session,
            actor=owner,
            action="setup",
            org=org,
            detail={"host": host or None, "modules": modules},
        )
        await session.commit()
        return SetupResult(slug=org.slug, host=org.custom_domain)
