"""Resolve an API-key request into the same ``RequestContext`` the session path yields (#20).

Called from ``require_context`` *after* the org is resolved from the hostname and RLS is bound,
so the key lookup is already tenant-scoped: a key whose org doesn't match the host simply isn't
found, which is exactly the rejection the issue asks for. Every downstream service then behaves
identically whether a browser session or a key authenticated the request.

A revoked or expired key returns ``401`` with the standard envelope — never ``403`` — so the
response never confirms that the key exists.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apikeys import keys as keygen
from app.core.apikeys.models import PRINCIPAL_USER, ApiKey, ServiceAccount
from app.core.auth.models import User
from app.core.cache import get_redis
from app.core.models import Membership, Org
from app.core.permissions.models import MembershipRole, RolePermission
from app.core.permissions.permset import PermissionSet
from app.errors import AppError

logger = logging.getLogger("schakl.apikeys")

#: Requests per key per minute. Generous for automation, a firm ceiling against a leaked key.
RATE_LIMIT_PER_MINUTE = 600
_LAST_USED_KEY = "schakl:apikey:lastused"


def extract_presented_key(request: Request) -> str | None:
    """The raw key from ``X-API-Key`` or ``Authorization: Bearer schakl_…``.

    A session ``Authorization`` header (never used here) or a bearer that isn't one of ours is
    ignored, so this coexists with cookie auth without hijacking it.
    """
    header = request.headers.get("x-api-key")
    if header:
        return header.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].strip().startswith(keygen.TOKEN_PREFIX):
        return auth[7:].strip()
    return None


def _unauthorized() -> AppError:
    return AppError("unauthorized", "errors.unauthorized", status_code=401)


def _split_and_check(perms: PermissionSet, scope: str) -> bool:
    base, sep, suffix = scope.partition(":")
    return perms.has(base, suffix if sep else None)


async def resolve_api_key_context(
    request: Request, session: AsyncSession, org: Org
) -> RequestContextLike | None:
    from app.core.tenancy import RequestContext

    raw = extract_presented_key(request)
    if raw is None:
        return None
    parsed = keygen.parse(raw)
    if parsed is None:
        raise _unauthorized()
    prefix, secret = parsed

    key = await session.scalar(
        select(ApiKey).where(ApiKey.org_id == org.id, ApiKey.prefix == prefix)
    )
    # A missing key, a wrong secret, a revoked or expired one all fail the same way — 401, no
    # detail. The constant-time compare runs even on a miss would be ideal, but a missing prefix
    # already leaks nothing an attacker couldn't learn by trying.
    if key is None or not keygen.verify_secret(secret, key.hash):
        raise _unauthorized()
    if key.revoked_at is not None:
        raise _unauthorized()
    if key.expires_at is not None and key.expires_at <= datetime.now(UTC):
        raise _unauthorized()

    await _enforce_rate_limit(key.id)

    if key.principal_type == PRINCIPAL_USER:
        ctx = await _personal_context(session, org, key, RequestContext)
    else:
        ctx = await _service_account_context(session, org, key, RequestContext)

    await _mark_used(key)
    return ctx


async def _personal_context(session, org, key, RequestContext):  # noqa: ANN001
    """A personal key acts as its owner, with permissions re-capped to the owner's live grants."""
    owner = await session.get(User, key.user_id)
    if owner is None or not owner.is_active:
        raise _unauthorized()
    row = (
        await session.execute(
            select(
                Membership,
                func.array_agg(RolePermission.permission).filter(
                    RolePermission.permission.is_not(None)
                ),
            )
            .outerjoin(MembershipRole, MembershipRole.membership_id == Membership.id)
            .outerjoin(RolePermission, RolePermission.role_id == MembershipRole.role_id)
            .where(Membership.user_id == owner.id, Membership.org_id == org.id)
            .group_by(Membership.id)
        )
    ).first()
    if row is None:
        # The owner is no longer a member here — the key dies with the membership.
        raise _unauthorized()
    membership, granted = row
    owner_perms = PermissionSet.of(granted)
    # Effective = key.scopes ∩ owner's live permissions, re-evaluated every request: a demoted
    # member's key is demoted with them.
    effective = [s for s in key.scopes if _split_and_check(owner_perms, s)]
    # The company horizon (#191) rides the owner's membership, exactly like a session —
    # a personal key must not see further than the person it acts as.
    from app.core.portal import portal_user_ids
    from app.core.scope import resolve_company_scope

    company_scope = (
        None
        if owner_perms.wildcard
        else await resolve_company_scope(session, org.id, membership.id)
    )
    is_portal = (
        False
        if owner_perms.wildcard
        else bool(await portal_user_ids(session, org.id, {owner.id}))
    )
    return RequestContext(
        user=owner,
        org=org,
        session=session,
        membership_id=membership.id,
        permissions=PermissionSet.of(effective),
        company_scope=company_scope,
        is_portal=is_portal,
    )


async def _service_account_context(session, org, key, RequestContext):  # noqa: ANN001
    """A service-account key acts as a synthetic principal, with exactly its granted scopes."""
    account = await session.get(ServiceAccount, key.service_account_id)
    if account is None or not account.active:
        raise _unauthorized()
    # A transient, unpersisted principal: its id is the service account's, so nothing it reads is
    # mistaken for a person's rows. Scopes were capped by the creator at mint time.
    principal = User(id=account.id, email=f"service-account:{account.id}", hashed_password="")
    principal.is_active = True
    return RequestContext(
        user=principal,
        org=org,
        session=session,
        membership_id=None,
        permissions=PermissionSet.of(list(key.scopes)),
    )


async def _enforce_rate_limit(key_id) -> None:  # noqa: ANN001
    """Fixed-window per-key limit in Redis. Fails **open** if Redis is unreachable — a rate limit
    is a safeguard, not an availability dependency."""
    try:
        redis = get_redis()
        window = int(datetime.now(UTC).timestamp() // 60)
        bucket = f"schakl:ratelimit:apikey:{key_id}:{window}"
        count = await redis.incr(bucket)
        if count == 1:
            await redis.expire(bucket, 120)
        if count > RATE_LIMIT_PER_MINUTE:
            raise AppError("rate_limited", "errors.rate_limited", status_code=429)
    except AppError:
        raise
    except Exception:  # noqa: BLE001 - never let a Redis hiccup break authentication
        logger.debug("api-key rate limit skipped (redis unavailable)", exc_info=True)


async def _mark_used(key: ApiKey) -> None:
    """Record last-use in Redis for a cron to flush to the DB — never a write on the hot path.

    The value carries the org id so the flush can bind RLS per tenant before writing.
    """
    try:
        value = f"{key.org_id}|{datetime.now(UTC).isoformat()}"
        await get_redis().hset(_LAST_USED_KEY, str(key.id), value)
    except Exception:  # noqa: BLE001
        logger.debug("api-key last-used mark skipped (redis unavailable)", exc_info=True)


# Typing convenience: the real return type is app.core.tenancy.RequestContext, imported lazily
# to avoid a cycle (tenancy imports this module).
RequestContextLike = object
