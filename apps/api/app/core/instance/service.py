"""Org lifecycle operations (issue #26): create, rename, re-slug, suspend, delete, purge.

Every mutation is audited. Org-scoped side effects (``org_settings``, ``memberships``) are
written with the RLS GUC bound to that one org — instance admin never gets a session that
can see two tenants at once.
"""

from __future__ import annotations

import logging
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
from app.core.entitlements.service import (
    OrgPlan,
    ensure_modules_enableable,
    ensure_requirements_met,
)
from app.core.instance import audit, repo
from app.core.models import Org, OrgSettings, OrgStatus
from app.core.permissions.catalog import ROLE_OWNER
from app.core.permissions.service import create_membership, seed_system_roles
from app.db import set_current_org
from app.errors import AppError

logger = logging.getLogger(__name__)

_password_hash = PasswordHash.recommended()

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# Hostnames with a meaning of their own under <base_domain>: the app itself, its API, and
# common infrastructure names. An org slugged "app" would shadow the canonical install host.
# The cloud posture adds its own: "edge" is the Cloudflare for SaaS fallback origin every
# custom hostname routes through, and "console" is the instance console — an org taking either
# would break the whole instance, not just itself (epic #199).
_RESERVED_SLUGS = frozenset(
    {
        "app", "api", "www", "mail", "traefik", "setup",
        "edge", "console", "admin", "mx", "ns", "ns1", "ns2", "smtp", "imap",
        "autodiscover", "autoconfig", "cdn", "static", "assets", "status", "_dmarc",
    }
)


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
    # An integration without the module it attaches to (CLAUDE.md §6a). Here as well as inside
    # ``ensure_modules_enableable`` because the first-run wizard reaches this function and not
    # that one: a box could otherwise be *installed* into the state the settings screen refuses.
    ensure_requirements_met(modules)
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
    email_included: bool = True,
) -> Org:
    slug = validate_slug(slug)
    if await repo.slug_taken(session, slug):
        raise AppError("slug_taken", "errors.slug_taken", status_code=409)
    # Cheap check first, before any row is written: a name already present in the operator's
    # DNS zone is not ours to take, even when no org holds the slug (an infrastructure record,
    # or a leftover from an org purged on another instance sharing the zone).
    await _assert_subdomain_free(slug)
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
    # Licensed modules need a covering license even on the instance-admin path (issue #137).
    await ensure_modules_enableable(modules, current=[])

    # Included e-mail (epic #199) is an entitlement, so it is set at provisioning time and
    # only from here — the tenant chooses whether to *use* the operator's transport, never
    # whether they have it. Default on: an org nobody made a decision about gets it.
    org = Org(slug=slug, name=name, email_included=email_included)
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
    await session.flush()
    # A new org gets the four system roles before anyone can be a member of it (issue #19).
    await seed_system_roles(session, org.id)

    detail: dict[str, Any] = {
        "name": name,
        "modules": modules,
        "email_included": email_included,
    }
    if owner_email:
        owner = await _get_or_create_user(session, owner_email)
        await create_membership(session, org.id, owner.id, ROLE_OWNER)
        detail["owner_email"] = owner_email
    # Last, so a Cloudflare failure rolls the whole org back rather than leaving one behind
    # with no address. Fail closed: a provisioned org that does not resolve is worse than a
    # provisioning call the billing system can simply retry.
    org.cf_dns_record_id = await _create_subdomain_record(slug)
    await session.flush()
    await audit.record(session, actor=actor, action="org.create", org=org, detail=detail)
    return org


async def _assert_subdomain_free(slug: str) -> None:
    """Refuse a slug whose subdomain already exists in the operator's zone (epic #199).

    No-op unless Cloudflare is configured — self-host resolves ``<slug>.<base_domain>`` through
    whatever the operator's own DNS says, which is not ours to inspect.
    """
    from app.core.cloud.cloudflare import (
        CloudflareError,
        CloudflareNotEntitledError,
        cloudflare_configured,
        find_dns_record,
        subdomain_for,
    )

    if not cloudflare_configured():
        return
    try:
        existing = await find_dns_record(subdomain_for(slug))
    except CloudflareNotEntitledError as exc:
        raise _not_entitled(exc, "read the zone's DNS records") from exc
    except CloudflareError as exc:
        raise AppError(
            "cloudflare_failed", "errors.cloudflare_failed", status_code=502
        ) from exc
    if existing is not None:
        raise AppError("subdomain_taken", "errors.subdomain_taken", status_code=409)


def _not_entitled(exc: Exception, what: str) -> AppError:
    """Map a Cloudflare permission/entitlement refusal to its own code (#293).

    Distinct from ``cloudflare_failed`` because the two need opposite responses: a transient
    edge failure is worth retrying, a missing token scope or plan entitlement never is. The
    operator sees Cloudflare's own words plus which call was refused; the caller sees a code
    that does not tell them to try again.
    """
    logger.error("cloudflare refused to %s — operator action required: %s", what, exc)
    return AppError("cloudflare_not_entitled", "errors.cloudflare_not_entitled", status_code=502)


async def _create_subdomain_record(slug: str) -> str | None:
    """Create the org's proxied subdomain record; None when Cloudflare is not configured."""
    from app.core.cloud.cloudflare import (
        CloudflareError,
        CloudflareNotEntitledError,
        cloudflare_configured,
        create_subdomain_record,
    )

    if not cloudflare_configured():
        return None
    try:
        return await create_subdomain_record(slug)
    except CloudflareNotEntitledError as exc:
        raise _not_entitled(exc, f"create the DNS record for {slug}") from exc
    except CloudflareError as exc:
        raise AppError(
            "cloudflare_failed", "errors.cloudflare_failed", status_code=502
        ) from exc


async def _delete_subdomain_record(record_id: str | None, slug: str) -> None:
    """Best-effort removal of a subdomain record. Never raises into the caller.

    The opposite trade-off from creation, and for the same reason as clearing a custom domain:
    the operations that call this (re-slug, terminate) must complete. A leftover CNAME points
    at the edge and resolves to an org that no longer answers for it, which the app rejects as
    an unknown host (CLAUDE.md §5) — recoverable, and visible from the record's own comment.
    """
    from app.core.cloud.cloudflare import (
        CloudflareError,
        cloudflare_configured,
        delete_dns_record,
    )

    if not record_id or not cloudflare_configured():
        return
    try:
        await delete_dns_record(record_id)
    except CloudflareError:
        logger.exception("could not delete cloudflare DNS record for %s", slug)


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
    email_included: bool | None = None,
) -> Org:
    _ensure_not_deleted(org)
    changes: dict[str, Any] = {}
    if email_included is not None and email_included != org.email_included:
        # Withdrawing it can stop an org's mail (they fall back to it silently), so the change
        # goes on the instance audit trail like every other entitlement.
        changes["email_included"] = {"from": org.email_included, "to": email_included}
        org.email_included = email_included
    if name is not None and name != org.name:
        changes["name"] = {"from": org.name, "to": name}
        org.name = name
    if slug is not None and slug != org.slug:
        slug = validate_slug(slug)
        if await repo.slug_taken(session, slug, exclude_org_id=org.id):
            raise AppError("slug_taken", "errors.slug_taken", status_code=409)
        await _assert_subdomain_free(slug)
        # Move the subdomain with the slug: create the new record before dropping the old, so a
        # failure halfway leaves the org reachable at its current address rather than at none.
        previous_record = org.cf_dns_record_id
        org.cf_dns_record_id = await _create_subdomain_record(slug)
        await _delete_subdomain_record(previous_record, org.slug)
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
    # Newly enabling a licensed module needs a covering license; keeping one enabled does
    # not — the write gate governs the already-enabled case (issue #137).
    await ensure_modules_enableable(
        modules, current=list(org_settings.enabled_modules or []), plan=OrgPlan.of(org)
    )
    org_settings.enabled_modules = modules
    await session.flush()
    await audit.record(
        session, actor=actor, action="org.modules", org=org, detail={"modules": modules}
    )
    return org_settings
