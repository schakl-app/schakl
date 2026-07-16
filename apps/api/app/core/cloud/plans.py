"""Per-org plan state (issue #200 slice). Business-licensed — see this directory's LICENSE.

The plan lives on ``orgs`` (resolution-adjacent, so no RLS) and is written only from the
instance surface — the tenant never edits their own billing state. Three shapes, so the
operator has real choices:

- ``trial``    — free trial: ``trial_ends_at`` set; the daily cloud cron suspends past it.
- ``standard`` — paid: no local clock; the billing system suspends/activates over the
                 provisioning API when payment state changes.
- ``unlimited``— never expires; the cron never touches it (internal orgs, lifetime deals).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth.models import User
from app.core.cloud.models import PLANS
from app.core.instance import audit
from app.core.instance import service as org_service
from app.core.models import Org, OrgStatus
from app.errors import AppError


def default_trial_end(days: int | None = None) -> datetime:
    return datetime.now(UTC) + timedelta(days=days or settings.cloud_trial_days)


async def set_plan(
    session: AsyncSession,
    actor: User | audit.SystemActor,
    org: Org,
    *,
    plan: str,
    trial_days: int | None = None,
    trial_ends_at: datetime | None = None,
) -> Org:
    """Change an org's plan. A move to ``trial`` (re)arms the clock; any other plan clears
    it. Recorded on the instance audit trail with before/after."""
    if plan not in PLANS:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"plan": "errors.validation"},
        )
    before = {"plan": org.plan, "trial_ends_at": _iso(org.trial_ends_at)}
    org.plan = plan
    if plan == "trial":
        org.trial_ends_at = trial_ends_at or default_trial_end(trial_days)
    else:
        org.trial_ends_at = None
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="org.plan",
        org=org,
        detail={
            "from": before,
            "to": {"plan": org.plan, "trial_ends_at": _iso(org.trial_ends_at)},
        },
    )
    return org


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def suspend_expired_trials(
    session: AsyncSession, actor: User | audit.SystemActor | None = None
) -> int:
    """Suspend every active org whose trial has run out. Idempotent — a suspended org does
    not transition again. Returns how many were suspended (the cron logs it)."""
    actor = actor or audit.SystemActor("system")
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(Org).where(
                Org.plan == "trial",
                Org.trial_ends_at.is_not(None),
                Org.trial_ends_at < now,
                Org.status == OrgStatus.ACTIVE.value,
            )
        )
    ).scalars()
    count = 0
    for org in rows:
        org.status = OrgStatus.SUSPENDED.value
        org.suspended_at = now
        await session.flush()
        await audit.record(
            session,
            actor=actor,
            action="org.trial_expired",
            org=org,
            detail={"trial_ends_at": _iso(org.trial_ends_at)},
        )
        count += 1
    return count


# Re-exported so callers need one import for "create with a plan".
validate_slug = org_service.validate_slug
