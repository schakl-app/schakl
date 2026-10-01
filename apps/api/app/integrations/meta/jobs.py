"""Background work: publishing what is due, and keeping the tokens alive.

Three jobs, and each exists because of a thing nobody would otherwise notice.

:func:`meta_publish_due` is the clock. It runs every minute and publishes what has come due,
hands Meta's scheduler what it may now be handed, and **looks** at every delivery that was
claimed and never answered. Nothing in the repo defers a job for days and Redis is not what
remembers a client's campaign: the row is the schedule, the sweep is what reads it.

:func:`meta_refresh_tokens` is the reason a token does not expire silently. A system-user
token lives sixty days and cannot be refreshed once it is dead, so it is refreshed with twenty
days to spare — and a refresh that keeps failing is *reported*, at fourteen, seven and one day,
to the people who can generate a new one.

:func:`meta_observe` refreshes what is mirrored about the credentials, nightly.

The licence check is the job's own (the mount-time gate only covers requests), through the
helper that knows the cloud posture (#387): expired means read-only, so nothing new is
published from here while it lasts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import run_per_org, system_context
from app.core.models import Org
from app.db import async_session_maker, set_current_org
from app.integrations.meta.models import (
    CredentialStatus,
    MetaCredential,
    MetaPost,
    MetaPostTarget,
    PostStatus,
    TargetStatus,
)
from app.integrations.meta.publisher import Publisher
from app.integrations.meta.service import MetaService, days_left, refresh_due

logger = logging.getLogger("schakl.meta")

SKU = "meta"
EVENT_TOKEN_EXPIRING = "meta.token_expiring"  # noqa: S105 — an event name, not a secret
ERROR_TOKEN_EXPIRED = "meta.error.token_expired"  # noqa: S105 — an i18n key


# --- publishing ------------------------------------------------------------------------------ #
async def _has_work(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """One indexed probe, so an org with nothing planned costs a single cheap query a minute."""
    found = await session.scalar(
        select(MetaPostTarget.id)
        .join(MetaPost, MetaPost.id == MetaPostTarget.post_id)
        .where(
            MetaPostTarget.org_id == org_id,
            MetaPost.status.in_((PostStatus.SCHEDULED.value, PostStatus.PUBLISHING.value)),
            MetaPostTarget.status.in_(
                (
                    TargetStatus.PENDING.value,
                    TargetStatus.PUBLISHING.value,
                    TargetStatus.HANDED_OVER.value,
                )
            ),
        )
        .limit(1)
    )
    return found is not None


async def _publish_org(org: Org, session: AsyncSession) -> None:
    if not await _has_work(session, org.id):
        return
    touched = await Publisher(system_context(org, session)).run_due()
    if touched:
        logger.info("meta: %s deliveries handled for org %s", touched, org.slug)


async def meta_publish_due(ctx: dict) -> None:
    """Every minute: publish what is due, in every org."""
    if not await sku_cron_enabled(SKU):
        return
    await run_per_org(_publish_org)


async def meta_publish_post(ctx: dict, org_id: str, post_id: str) -> None:
    """One post, now — what "publish now" and "schedule" wake the worker with.

    A nicety over the sweep, never a replacement for it: if this job is lost the minute tick
    finds the same post, because the row is the schedule.
    """
    if not await sku_cron_enabled(SKU):
        return
    async with async_session_maker() as session:
        org = await session.get(Org, uuid.UUID(org_id))
        if org is None:
            return
        await set_current_org(session, org.id)
        try:
            await Publisher(system_context(org, session)).run_due(post_id=uuid.UUID(post_id))
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("meta: publishing post %s failed", post_id)


# --- tokens ---------------------------------------------------------------------------------- #
async def refresh_org_tokens(org: Org, session: AsyncSession) -> None:
    """Refresh what is due in one org, and warn about what could not be."""
    ctx = system_context(org, session)
    service = MetaService(ctx)
    ids = (
        await session.scalars(
            select(MetaCredential.id).where(
                MetaCredential.org_id == org.id, MetaCredential.active.is_(True)
            )
        )
    ).all()
    for credential_id in ids:
        try:
            row = await service.get_credential(credential_id)
            now = datetime.now(UTC)
            if row.expires_at is not None and row.expires_at <= now:
                if row.status != CredentialStatus.EXPIRED.value:
                    row.status = CredentialStatus.EXPIRED.value
                    row.last_error = ERROR_TOKEN_EXPIRED
                    await _warn(ctx, session, row, stage=0)
            elif refresh_due(row, now):
                await service.refresh(credential_id)
                stage = service.warning_stage(row, datetime.now(UTC))
                if row.refresh_error and stage is not None:
                    await _warn(ctx, session, row, stage=stage)
                    row.warned_days = stage
            # One credential's outcome is durable before the next is touched: a rollback for
            # the third must not discard the token the first was just given (#387's sibling).
            await session.commit()
            await set_current_org(session, org.id)
        except Exception:  # noqa: BLE001 — one credential must never end the loop
            await session.rollback()
            await set_current_org(session, org.id)
            logger.exception("meta: token refresh failed for credential %s", credential_id)


async def _warn(ctx, session: AsyncSession, row: MetaCredential, *, stage: int) -> None:  # noqa: ANN001
    """Tell the people who can generate a token that this one is running out.

    The recipients are named (SnelStart's rule): nobody watches a credential, so an event with
    no hint would be written for an audience of nobody. Imported inside the function (§6).
    """
    recipients = await _managers(session, row.org_id)
    if not recipients:
        logger.info("meta: credential %s is expiring and nobody holds the key", row.id)
        return
    from app.modules.notifications.service import NotificationService

    left = days_left(row.expires_at)
    expiry = row.expires_at.date().isoformat() if row.expires_at else ""
    try:
        await NotificationService(ctx).ingest(
            EVENT_TOKEN_EXPIRING,
            "meta_credential",
            row.id,
            {
                "label": row.label,
                "days": max(left or 0, 0),
                "expired": stage == 0,
                "_recipients": recipients,
                "_dedup_key": f"{EVENT_TOKEN_EXPIRING}:{row.id}:{stage}:{expiry}",
            },
        )
    except Exception:  # noqa: BLE001 — a notification failure must not lose the refresh
        logger.warning("meta: could not notify about credential %s", row.id)


async def _managers(session: AsyncSession, org_id) -> list:  # noqa: ANN001
    """Everyone in this org who may administer the Meta connection (the owner's ``*`` too)."""
    from app.core.models import Membership
    from app.core.permissions.models import MembershipRole, RolePermission

    rows = await session.execute(
        select(Membership.user_id)
        .join(MembershipRole, MembershipRole.membership_id == Membership.id)
        .join(RolePermission, RolePermission.role_id == MembershipRole.role_id)
        .where(
            Membership.org_id == org_id,
            RolePermission.org_id == org_id,
            RolePermission.permission.in_(["*", "meta.settings.manage"]),
        )
        .distinct()
    )
    return [row[0] for row in rows]


async def meta_refresh_tokens(ctx: dict) -> None:
    """Nightly: refresh every token with less than three weeks left, and say so when it fails.

    Deliberately **not** gated on the licence. An expired licence makes the integration
    read-only; it must not also let the agency's tokens die, because a dead token cannot be
    refreshed and the day the licence is renewed every client would need a new one generated
    by hand. Gate what the agency does, never the upkeep of what it already has.
    """
    await run_per_org(refresh_org_tokens)


# --- observation ----------------------------------------------------------------------------- #
async def _observe_org(org: Org, session: AsyncSession) -> None:
    ctx = system_context(org, session)
    service = MetaService(ctx)
    ids = (
        await session.scalars(
            select(MetaCredential.id).where(
                MetaCredential.org_id == org.id, MetaCredential.active.is_(True)
            )
        )
    ).all()
    for credential_id in ids:
        try:
            await service.observe_credential(credential_id)
            await session.commit()
            await set_current_org(session, org.id)
        except Exception:  # noqa: BLE001
            await session.rollback()
            await set_current_org(session, org.id)
            logger.exception("meta: observe failed for credential %s", credential_id)


async def meta_observe(ctx: dict) -> None:
    """Nightly: ask Meta what each token is, so the screen's scopes and expiry are last
    night's rather than the day the token was pasted in."""
    if not await sku_cron_enabled(SKU):
        return
    await run_per_org(_observe_org)
