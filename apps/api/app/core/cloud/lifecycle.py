"""Per-org end date, grace, and termination (epic #199). Business-licensed — see LICENSE.

An org may carry an ``ends_at``. **NULL means unlimited** and nothing here touches it — the
default for machinery that ends in irreversible deletion must be "never". Past the date:

======================  ==============================================  ==================
stage                   what the tenant experiences                     recoverable
======================  ==============================================  ==================
``active``              nothing; before ``ends_at``                      —
``warning``             full access, plus a banner and an e-mail         yes
``suspended``           login renders, every request refused             yes
(terminated)            the org and its data are gone                    from the archive
======================  ==============================================  ==================

The suspended window exists deliberately: it is the last state from which a mistake — a wrong
end date, a customer who paid late — is fixable by flipping one field, and it costs one extra
column to have. Going from "fully working" straight to "permanently deleted" leaves no such
moment.

**Two switches, because the last step cannot be undone.** ``SCHAKL_CLOUD_LIFECYCLE_ENABLED``
runs the sweep at all; ``SCHAKL_CLOUD_LIFECYCLE_DESTRUCTIVE`` allows the purge. Deploying
enabled-but-not-destructive gives real warnings and suspensions with nothing destroyed, which
is how the dates and the copy get checked against live orgs before anything is unrecoverable.

Termination is ordered so that **every** failure mode is safe (worst case an orphaned archive
or a re-run) and never a purge behind a failed export:

1. mark deleted — so the archive is taken against frozen data, and ``purge_org``'s
   export-since-soft-delete precondition can be satisfied honestly rather than bypassed
2. archive rows **and bytes** to the storage backend, outside the tenant key space
3. remove the Cloudflare custom hostname and the subdomain DNS record
4. delete the org's stored bytes
5. purge the rows

Anything that raises stops the sequence before step 5; the org stays soft-deleted and the next
sweep retries. A soft-deleted org resolves for nobody, so a retry loop is inert, not harmful.
"""

from __future__ import annotations

import io
import logging
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.email.senders import OutgoingEmail
from app.core.email.service import send_org_email
from app.core.instance import audit, portability
from app.core.instance import service as org_service
from app.core.models import Membership, Org, OrgSettings, OrgStatus
from app.db import set_current_org
from app.i18n import translate

logger = logging.getLogger(__name__)

STAGE_ACTIVE = "active"
STAGE_WARNING = "warning"
STAGE_SUSPENDED = "suspended"

#: Where termination archives land: a prefix outside every org's key space, so
#: ``delete_prefix(org_id)`` in step 4 cannot take the archive it just wrote with it.
ARCHIVE_PREFIX = "archive"


class LifecycleUpdate(BaseModel):
    """What an operator may *set*: the end date and its two windows, nothing else.

    Kept separate from the response shape on purpose — ``lifecycle_stage``, ``suspends_at`` and
    ``terminates_at`` are derived, and accepting them would let a caller believe they had moved
    a stage by hand when the value was silently ignored.
    """

    ends_at: datetime | None = None
    grace_days: int | None = Field(default=None, ge=0, le=3650)
    retention_days: int | None = Field(default=None, ge=0, le=3650)


def grace_days(org: Org) -> int:
    return org.grace_days if org.grace_days is not None else settings.cloud_grace_days


def retention_days(org: Org) -> int:
    return (
        org.retention_days if org.retention_days is not None else settings.cloud_retention_days
    )


def suspend_at(org: Org) -> datetime | None:
    return org.ends_at + timedelta(days=grace_days(org)) if org.ends_at else None


def terminate_at(org: Org) -> datetime | None:
    started = suspend_at(org)
    return started + timedelta(days=retention_days(org)) if started else None


def stage_for(org: Org, now: datetime) -> str:
    """Which stage ``now`` falls in. ``active`` for an unlimited org, always."""
    if org.ends_at is None or now < org.ends_at:
        return STAGE_ACTIVE
    if now < suspend_at(org):
        return STAGE_WARNING
    return STAGE_SUSPENDED


def is_due_for_termination(org: Org, now: datetime) -> bool:
    ends = terminate_at(org)
    return ends is not None and now >= ends


async def set_lifecycle(
    session: AsyncSession,
    actor,  # noqa: ANN001 — User | audit.SystemActor, as everywhere on this trail
    org: Org,
    *,
    ends_at: datetime | None,
    grace: int | None = None,
    retention: int | None = None,
) -> Org:
    """Set an org's end date and windows. ``ends_at=None`` means unlimited.

    Clearing or moving the date **forward re-arms the stage**: an org that was warned, and is
    now given more time, must stop being warned — otherwise the banner outlives the reason for
    it and the next sweep would not re-fire the notification when the new date arrives.
    """
    before = {
        "ends_at": org.ends_at.isoformat() if org.ends_at else None,
        "grace_days": org.grace_days,
        "retention_days": org.retention_days,
    }
    org.ends_at = ends_at
    org.grace_days = grace
    org.retention_days = retention
    if ends_at is None or stage_for(org, datetime.now(UTC)) == STAGE_ACTIVE:
        org.lifecycle_stage = STAGE_ACTIVE
        org.lifecycle_notified_at = None
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="org.lifecycle_set",
        org=org,
        detail={
            "from": before,
            "to": {
                "ends_at": ends_at.isoformat() if ends_at else None,
                "grace_days": grace,
                "retention_days": retention,
            },
        },
    )
    return org


async def _owner_emails(session: AsyncSession, org: Org) -> list[str]:
    """Who hears about an ending org: the org's own members, not the instance operator."""
    from app.core.auth.models import User

    await set_current_org(session, org.id)
    rows = await session.execute(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org.id, User.is_active.is_(True))
    )
    return sorted({email for (email,) in rows if email})


async def _notify(session: AsyncSession, org: Org, stage: str) -> None:
    """Tell the org its access is ending. Never raises — a mail outage must not stall a sweep
    (and must certainly not stall it *before* the suspend that the mail announces)."""
    try:
        await set_current_org(session, org.id)
        row = await session.scalar(select(OrgSettings).where(OrgSettings.org_id == org.id))
        locale = (row.default_locale if row else None) or settings.default_locale
        key = "cloud.lifecycle.email_warning" if stage == STAGE_WARNING else (
            "cloud.lifecycle.email_suspended"
        )
        when = terminate_at(org)
        body = translate(
            key,
            locale,
            brand=(row.brand_name if row else org.name),
            date=when.date().isoformat() if when else "",
        )
        subject = translate(f"{key}_subject", locale, brand=(row.brand_name if row else org.name))
        for address in await _owner_emails(session, org):
            await send_org_email(
                session, org.id, OutgoingEmail(to=address, subject=subject, text=body)
            )
    except Exception:  # noqa: BLE001 — notification is best effort by contract
        logger.exception("lifecycle notification failed for org %s", org.slug)


async def _archive_to_storage(session: AsyncSession, org: Org) -> str:
    """Write the complete archive (rows + bytes) and return its key. Raises on failure — this
    is the step the whole sequence is allowed to stop on."""
    import asyncio

    from app.core.storage.backend import get_storage

    blob = await portability.build_archive(session, org)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    # The suffix is not decoration: at second resolution two archives taken in the same second
    # collide, and the second would silently overwrite the first. These may be the last copy of
    # a departed customer's data, so the key must never be reusable by accident.
    key = f"{ARCHIVE_PREFIX}/{org.id}/{stamp}-{uuid.uuid4().hex[:8]}.zip"
    await asyncio.to_thread(get_storage().put, key, io.BytesIO(blob))
    return key


async def _release_cloudflare(org: Org) -> None:
    """Drop the org's Cloudflare records. Best effort: a leftover record routes to an org that
    no longer answers, which the app rejects as an unknown host — recoverable, unlike a purge
    blocked forever by an unreachable API."""
    from app.core.cloud.cloudflare import (
        CloudflareError,
        cloudflare_configured,
        delete_custom_hostname,
        delete_dns_record,
    )

    if not cloudflare_configured():
        return
    for delete, value in (
        (delete_custom_hostname, org.cf_hostname_id),
        (delete_dns_record, org.cf_dns_record_id),
    ):
        if not value:
            continue
        try:
            await delete(value)
        except CloudflareError:
            logger.exception("lifecycle: cloudflare cleanup failed for %s", org.slug)


async def _delete_stored_bytes(org: Org) -> int:
    """Reclaim the org's blobs. Best effort for the same reason as Cloudflare: orphaned bytes
    cost money, a purge that can never run costs correctness."""
    import asyncio

    from app.core.storage.backend import StorageUnavailableError, get_storage

    try:
        return await asyncio.to_thread(get_storage().delete_prefix, str(org.id))
    except (StorageUnavailableError, OSError, ValueError):
        logger.exception("lifecycle: could not delete stored bytes for %s", org.slug)
        return 0


def _already_archived(org: Org) -> bool:
    """Whether a usable archive exists for the org's current soft-deleted state — the same
    condition ``purge_org`` checks before it will destroy anything."""
    return org.exported_at is not None and (
        org.deleted_at is None or org.exported_at >= org.deleted_at
    )


def _termination_work(org: Org) -> bool:
    """Whether running :func:`terminate` on this org would actually do anything.

    With the destructive switch off, an org that was archived on a previous run is finished:
    without this the nightly sweep would write one full copy of it per day, and would spend its
    whole batch budget on orgs it cannot progress.
    """
    if settings.cloud_lifecycle_destructive:
        return True
    return not (org.status == OrgStatus.DELETED.value and _already_archived(org))


async def terminate(session: AsyncSession, org: Org) -> bool:
    """Run the full termination for one org. Returns whether the rows were purged.

    With ``cloud_lifecycle_destructive`` off this stops after the archive, leaving the org
    soft-deleted and recoverable — the intended first deployment.
    """
    actor = audit.SystemActor("system")
    if org.status != OrgStatus.DELETED.value:
        await org_service.set_status(session, actor, org, OrgStatus.DELETED)

    key = await _archive_to_storage(session, org)
    org.exported_at = datetime.now(UTC)
    await session.flush()
    await audit.record(
        session, actor=actor, action="org.lifecycle_archived", org=org, detail={"key": key}
    )

    if not settings.cloud_lifecycle_destructive:
        logger.info("lifecycle: %s archived; purge withheld (destructive off)", org.slug)
        return False

    await _release_cloudflare(org)
    removed = await _delete_stored_bytes(org)
    slug = org.slug
    await audit.record(
        session,
        actor=actor,
        action="org.lifecycle_terminated",
        org=org,
        detail={"archive": key, "blobs_deleted": removed},
    )
    await org_service.purge_org(session, actor, org, confirm=slug)
    logger.info("lifecycle: purged org %s (archive %s, %d blobs)", slug, key, removed)
    return True


async def sweep(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Advance every org with an end date. Idempotent: a second run in the same window is a
    no-op, because each transition is guarded on ``lifecycle_stage``."""
    now = now or datetime.now(UTC)
    counts = {"warned": 0, "suspended": 0, "terminated": 0}
    if not settings.cloud_lifecycle_enabled:
        return counts

    actor = audit.SystemActor("system")
    orgs = (
        (
            await session.execute(
                select(Org)
                .where(Org.ends_at.is_not(None), Org.ends_at <= now)
                .order_by(Org.ends_at.asc())
            )
        )
        .scalars()
        .all()
    )

    #: Bounds the *work attempted*, not the purges completed — with the destructive switch off
    #: nothing is ever purged, and a bound on purges would then bound nothing at all.
    attempted = 0

    for org in orgs:
        if org.status == OrgStatus.DELETED.value and org.deleted_at and not is_due_for_termination(
            org, now
        ):
            # Soft-deleted by hand, not by us: leave the operator's decision alone.
            continue
        try:
            if is_due_for_termination(org, now):
                if not _termination_work(org):
                    continue
                if attempted >= settings.cloud_lifecycle_batch:
                    continue
                attempted += 1
                if await terminate(session, org):
                    counts["terminated"] += 1
                await session.commit()
                continue

            stage = stage_for(org, now)
            if stage == org.lifecycle_stage:
                continue
            if stage == STAGE_WARNING:
                org.lifecycle_stage = STAGE_WARNING
                org.lifecycle_notified_at = now
                await session.flush()
                await audit.record(
                    session, actor=actor, action="org.lifecycle_warning", org=org,
                    detail={"ends_at": org.ends_at.isoformat()},
                )
                await _notify(session, org, STAGE_WARNING)
                counts["warned"] += 1
            elif stage == STAGE_SUSPENDED:
                org.lifecycle_stage = STAGE_SUSPENDED
                org.lifecycle_notified_at = now
                if org.status == OrgStatus.ACTIVE.value:
                    await org_service.set_status(session, actor, org, OrgStatus.SUSPENDED)
                await session.flush()
                await audit.record(
                    session, actor=actor, action="org.lifecycle_suspended", org=org,
                    detail={"terminates_at": terminate_at(org).isoformat()},
                )
                await _notify(session, org, STAGE_SUSPENDED)
                counts["suspended"] += 1
            await session.commit()
        except Exception:  # noqa: BLE001 — one bad org must not stop the sweep
            logger.exception("lifecycle sweep failed for org %s", org.slug)
            await session.rollback()
    return counts
