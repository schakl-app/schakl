"""Org lifecycle operations (issue #26): create, rename, re-slug, suspend, delete, purge.

Every mutation is audited. Org-scoped side effects (``org_settings``, ``memberships``) are
written with the RLS GUC bound to that one org — instance admin never gets a session that
can see two tenants at once.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth.models import User
from app.core.instance import audit, repo
from app.core.models import Membership, Org, OrgSettings, OrgStatus
from app.core.roles import Role
from app.db import set_current_org
from app.errors import AppError

_password_hash = PasswordHash.recommended()

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# Hostnames with a meaning of their own under <base_domain>: the app itself, its API, and
# common infrastructure names. An org slugged "app" would shadow the canonical install host.
_RESERVED_SLUGS = frozenset({"app", "api", "www", "mail", "traefik", "setup"})


def validate_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not _SLUG_RE.fullmatch(slug) or slug in _RESERVED_SLUGS:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"slug": "errors.invalid_slug"},
        )
    return slug


def validate_modules(modules: list[str]) -> list[str]:
    available = set(settings.enabled_modules)
    if "companies" not in modules or any(m not in available for m in modules):
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"enabled_modules": "errors.validation"},
        )
    return modules


def _ensure_not_deleted(org: Org) -> None:
    if org.status == OrgStatus.DELETED.value:
        raise AppError("org_deleted", "errors.org_deleted", status_code=409)


async def create_org(
    session: AsyncSession,
    actor: User,
    *,
    name: str,
    slug: str,
    brand_name: str | None = None,
    locale: str | None = None,
    enabled_modules: list[str] | None = None,
    owner_email: str | None = None,
) -> Org:
    slug = validate_slug(slug)
    if await repo.slug_taken(session, slug):
        raise AppError("slug_taken", "errors.slug_taken", status_code=409)
    locale = locale or settings.default_locale
    if locale not in settings.supported_locales:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"locale": "errors.validation"},
        )
    modules = validate_modules(
        enabled_modules if enabled_modules is not None else list(settings.enabled_modules)
    )

    org = Org(slug=slug, name=name)
    session.add(org)
    await session.flush()

    await set_current_org(session, org.id)
    session.add(
        OrgSettings(
            org_id=org.id,
            brand_name=brand_name or name,
            default_locale=locale,
            enabled_modules=modules,
        )
    )

    detail: dict[str, Any] = {"name": name, "modules": modules}
    if owner_email:
        owner = await _get_or_create_user(session, owner_email)
        session.add(Membership(org_id=org.id, user_id=owner.id, role=Role.OWNER.value))
        detail["owner_email"] = owner_email
    await session.flush()
    await audit.record(session, actor=actor, action="org.create", org=org, detail=detail)
    return org


async def _get_or_create_user(session: AsyncSession, email: str) -> User:
    """Same pattern as a member invite: an unusable random password, set via forgot-password."""
    email = email.lower()
    user = await session.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=_password_hash.hash(secrets.token_urlsafe(24)),
            is_active=True,
            is_verified=False,
        )
        session.add(user)
        await session.flush()
    return user


async def update_org(
    session: AsyncSession,
    actor: User,
    org: Org,
    *,
    name: str | None = None,
    slug: str | None = None,
) -> Org:
    _ensure_not_deleted(org)
    changes: dict[str, Any] = {}
    if name is not None and name != org.name:
        changes["name"] = {"from": org.name, "to": name}
        org.name = name
    if slug is not None and slug != org.slug:
        slug = validate_slug(slug)
        if await repo.slug_taken(session, slug, exclude_org_id=org.id):
            raise AppError("slug_taken", "errors.slug_taken", status_code=409)
        changes["slug"] = {"from": org.slug, "to": slug}
        org.slug = slug
    if changes:
        await session.flush()
        await audit.record(session, actor=actor, action="org.update", org=org, detail=changes)
    return org


async def set_status(
    session: AsyncSession, actor: User, org: Org, status: OrgStatus
) -> Org:
    """One guarded transition per call; anything not listed is a 409."""
    now = datetime.now(UTC)
    current = OrgStatus(org.status)
    allowed = {
        (OrgStatus.ACTIVE, OrgStatus.SUSPENDED),
        (OrgStatus.SUSPENDED, OrgStatus.ACTIVE),
        (OrgStatus.ACTIVE, OrgStatus.DELETED),
        (OrgStatus.SUSPENDED, OrgStatus.DELETED),
        (OrgStatus.DELETED, OrgStatus.ACTIVE),  # restore
    }
    if (current, status) not in allowed:
        raise AppError("conflict", "errors.conflict", status_code=409)
    org.status = status.value
    org.suspended_at = now if status == OrgStatus.SUSPENDED else None
    org.deleted_at = now if status == OrgStatus.DELETED else None
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action=f"org.{status.value if status != OrgStatus.ACTIVE else 'activate'}",
        org=org,
        detail={"from": current.value, "to": status.value},
    )
    return org


async def purge_org(session: AsyncSession, actor: User, org: Org, *, confirm: str) -> None:
    """Hard delete. Only a soft-deleted org, only confirmed by slug, and only after an
    export taken **since** the soft delete — the data is frozen from that moment, so that
    export is provably complete (issue #26: "with export before destroy")."""
    if org.status != OrgStatus.DELETED.value:
        raise AppError("org_not_deleted", "errors.org_not_deleted", status_code=409)
    if confirm != org.slug:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"confirm": "errors.confirm_slug"},
        )
    if org.exported_at is None or (org.deleted_at and org.exported_at < org.deleted_at):
        raise AppError("export_required", "errors.export_required", status_code=409)

    # Audit first (same transaction): the FK nulls itself on delete, the slug snapshot stays.
    await audit.record(session, actor=actor, action="org.purge", org=org)
    # FK ON DELETE CASCADE wipes every org-scoped row; referential actions bypass RLS.
    await session.delete(org)
    await session.flush()


async def set_org_modules(
    session: AsyncSession, actor: User, org: Org, modules: list[str]
) -> OrgSettings:
    _ensure_not_deleted(org)
    modules = validate_modules(modules)
    await set_current_org(session, org.id)
    org_settings = await session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == org.id)
    )
    if org_settings is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    org_settings.enabled_modules = modules
    await session.flush()
    await audit.record(
        session, actor=actor, action="org.modules", org=org, detail={"modules": modules}
    )
    return org_settings
