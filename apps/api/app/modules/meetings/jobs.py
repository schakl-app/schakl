"""ARQ jobs for meetings: the pipeline run, the reaper, the audio retention sweep.

* ``meetings_process`` — one meeting: fold the pieces into the recording, transcribe it (in as
  many requests as the tenant's speech provider takes, ``pipeline.py``), draft the minutes
  (``minutes.py``). Enqueued by ``finish`` and by ``retry``.
* ``meetings_reap_stale`` — every quarter of an hour, per org. A row a worker claimed and never
  released — the process is not there any more — is failed, so the screen stops saying "bezig"
  (the #300 rule, the interactions module's shape).
* ``meetings_sweep_audio`` — nightly, per org. The recording of a confirmed meeting is dropped
  after :data:`AUDIO_RETENTION_DAYS`; the transcript and the minutes stay. A recording is the
  most personal thing this product stores and the AVG asks for a stated retention, so the
  screen states this number where the person presses record.

**The provider calls run outside any open transaction.** A worker holds a pooled connection of
its own, but a two-hour recording is minutes of provider time and the same pool serves the crons
beside it — so every step commits before the network call and touches the session again only
after it. ``AIService.complete`` wraps its call in ``ctx.release_db()`` on top, which here is a
commit and a re-bind: harmless, because the status write it commits is the one we wanted.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.candidates import gather as gather_candidates
from app.core.ai.providers import AIProviderError
from app.core.ai.service import AIService
from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import run_per_org, system_context
from app.core.models import Org, OrgStatus
from app.core.storage.backend import storage_for
from app.core.storage.models import StoredFile
from app.core.storage.service import drop_file
from app.core.storage.system import store_system_file
from app.core.timezone import org_today, org_zoneinfo
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.modules.meetings.minutes import FEATURE, draft_minutes
from app.modules.meetings.models import (
    ENTITY_TYPE,
    WORKER_STATUSES,
    Meeting,
    MeetingStatus,
)
from app.modules.meetings.pipeline import (
    CONTENT_TYPES,
    MAX_RECORDING_BYTES,
    fold_chunks,
    transcribe_recording,
)
from app.modules.meetings.service import CHUNK_PREFIX, drop_audio, org_locale

logger = logging.getLogger("schakl.meetings")

#: A run still claimed after this is claimed by nobody. Long: a three-hour recording through a
#: provider that takes a while is a legitimate forty minutes.
STALE_AFTER_MINUTES = 90
#: How long a confirmed meeting keeps its audio. Stated on the recording screen.
AUDIO_RETENTION_DAYS = 30


async def _licensed() -> bool:
    return await sku_cron_enabled("meetings")


async def _active_org(session: AsyncSession, org_id: str) -> Org | None:
    org = await session.get(Org, uuid.UUID(org_id))
    return org if org is not None and org.status == OrgStatus.ACTIVE.value else None


async def _load(
    session: AsyncSession, org: Org | uuid.UUID, meeting_id: uuid.UUID
) -> Meeting | None:
    org_id = org if isinstance(org, uuid.UUID) else org.id
    return await session.scalar(
        select(Meeting).where(Meeting.org_id == org_id, Meeting.id == meeting_id)
    )


async def _commit(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Commit, then bind the tenant again.

    The RLS GUC is transaction-local (``SET LOCAL``), so a commit ends it and the next query
    would run against no org — which under forced RLS is an empty answer that looks exactly
    like "not configured". Every commit in this job goes through here for that reason.
    """
    await session.commit()
    await set_current_org(session, org_id)


async def _set_status(
    session: AsyncSession,
    org_id: uuid.UUID,
    row: Meeting,
    status: str,
    *,
    error_key: str | None = None,
) -> None:
    row.status = status
    row.status_at = datetime.now(UTC)
    row.error_key = error_key
    await _commit(session, org_id)


async def _fold(ctx, session: AsyncSession, row: Meeting) -> tuple[bytes, str]:  # noqa: ANN001
    """The stored pieces into the one recording; returns its bytes and container extension.

    A retry over a meeting already folded reads the recording back instead — the pieces are
    gone by then.
    """
    extension = row.audio_format or "webm"
    if row.audio_file_id is not None:
        stored = await session.get(StoredFile, row.audio_file_id)
        if stored is not None:
            backend = storage_for(stored.backend)
            data = await asyncio.to_thread(lambda: backend.open(stored.storage_key).read())
            return data, extension
    pieces = (
        (
            await session.execute(
                select(StoredFile)
                .where(
                    StoredFile.org_id == ctx.org.id,
                    StoredFile.entity_type == ENTITY_TYPE,
                    StoredFile.entity_id == row.id,
                    StoredFile.content_id.like(f"{CHUNK_PREFIX}%"),
                )
                .order_by(StoredFile.content_id)
            )
        )
        .scalars()
        .all()
    )
    if not pieces:
        raise AppError("meetings_no_audio", "meetings.error.no_audio", status_code=422)
    chunks: list[bytes] = []
    for piece in pieces:
        backend = storage_for(piece.backend)
        chunks.append(
            await asyncio.to_thread(lambda p=piece, b=backend: b.open(p.storage_key).read())
        )
    data = fold_chunks(chunks)
    content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
    stored = await store_system_file(
        ctx,
        filename=f"{row.title[:80] or 'meeting'}.{extension}",
        content_type=content_type,
        data=data,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        created_by_user_id=row.owner_user_id,
        max_bytes=MAX_RECORDING_BYTES,
        allowed_types=frozenset(CONTENT_TYPES.values()),
    )
    if stored is None:
        raise AppError("meetings_too_large", "meetings.error.too_large", status_code=413)
    row.audio_file_id = stored.id
    for piece in pieces:
        await drop_file(ctx, piece)
    row.chunks_received = 0
    await _commit(session, ctx.org.id)
    return data, extension


async def run_pipeline(session: AsyncSession, org: Org, meeting_id: uuid.UUID) -> None:
    """The whole run for one meeting, ending on ``review`` or ``failed``. Never raises."""
    # Plain values, read once: a rollback on the error path expires every loaded object, and
    # an expired attribute read is a lazy load — sync IO the async session refuses.
    org_id = org.id
    row = await _load(session, org_id, meeting_id)
    if row is None or row.status != MeetingStatus.QUEUED.value:
        logger.info("meetings: %s is not queued; standing down", meeting_id)
        return
    ctx = system_context(org, session)
    await _set_status(session, org_id, row, MeetingStatus.TRANSCRIBING.value)
    service = AIService(ctx)
    try:
        config = await service.speech_config(FEATURE)
        await service.ensure_audio_budget()
        data, extension = await _fold(ctx, session, row)
        language, duration_hint = row.language, row.duration_seconds
        # --- the words: outside any transaction ------------------------------------- #
        await session.commit()
        try:
            transcribed = await transcribe_recording(
                config,
                data,
                extension,
                language=language,
                duration_seconds=duration_hint,
            )
        except AIProviderError as exc:
            logger.warning("meetings: transcription failed for %s: %s", meeting_id, exc)
            raise AppError(
                "ai_provider_error", "errors.ai_provider_error", status_code=502
            ) from exc
        await set_current_org(session, org_id)
        row = await _load(session, org_id, meeting_id)
        if row is None:
            return
        row.transcript = {
            "segments": transcribed.segments,
            "model": config.model,
            "parts": transcribed.parts,
        }
        row.transcript_text = transcribed.text or None
        if transcribed.seconds and not row.duration_seconds:
            row.duration_seconds = transcribed.seconds
        await service.record_usage(FEATURE, config.model, 0, 0, audio_seconds=transcribed.seconds)
        await _set_status(session, org_id, row, MeetingStatus.SUMMARISING.value)
        if not (transcribed.text or "").strip():
            raise AppError("meetings_no_speech", "meetings.error.no_speech", status_code=422)
        # --- the minutes --------------------------------------------------------------- #
        chat = await service.config_for(FEATURE)
        await service.ensure_budget()
        candidates = await gather_candidates(ctx, "", blocks=frozenset({"members"}))
        zone = await org_zoneinfo(session, org_id)
        today = await org_today(session, org_id)
        locale = await org_locale(ctx)
        draft = await draft_minutes(
            service,
            title=row.title,
            occurred_at=row.occurred_at,
            kind=row.kind,
            segments=list(transcribed.segments),
            transcript_text=transcribed.text,
            speakers=dict(row.speakers or {}),
            candidates=candidates,
            today=today,
            now=datetime.now(zone),
            locale=locale,
            agency=await _agency_name(session, org),
            duration=row.duration_seconds,
        )
        # ``complete`` released and re-bound the session; the row object is still ours.
        row = await _load(session, org_id, meeting_id) or row
        row.minutes = draft.model_dump(mode="json")
        row.transcript = {
            "segments": transcribed.segments,
            "model": config.model,
            "parts": transcribed.parts,
            "chat_model": chat.model,
        }
        await _set_status(session, org_id, row, MeetingStatus.REVIEW.value)
    except AppError as exc:
        logger.warning("meetings: %s failed: %s", meeting_id, exc.message_key)
        await _fail(session, org_id, meeting_id, exc.message_key)
    except Exception:
        logger.exception("meetings: pipeline crashed for %s", meeting_id)
        await _fail(session, org_id, meeting_id, "meetings.error.failed")


async def _fail(
    session: AsyncSession, org_id: uuid.UUID, meeting_id: uuid.UUID, error_key: str
) -> None:
    """Whatever half-written state the run left behind goes; the row says why it stopped."""
    await session.rollback()
    await set_current_org(session, org_id)
    row = await session.scalar(
        select(Meeting).where(Meeting.org_id == org_id, Meeting.id == meeting_id)
    )
    if row is not None:
        await _set_status(session, org_id, row, MeetingStatus.FAILED.value, error_key=error_key)


async def _agency_name(session: AsyncSession, org: Org) -> str:
    from app.core.models import OrgSettings

    brand = await session.scalar(
        select(OrgSettings.brand_name).where(OrgSettings.org_id == org.id)
    )
    return brand or org.name or "the agency"


async def meetings_process(ctx: dict, org_id: str, meeting_id: str) -> None:  # noqa: ARG001
    if not await _licensed():
        logger.info("meetings: sku not writable; %s not processed", meeting_id)
        return
    async with async_session_maker() as session:
        org = await _active_org(session, org_id)
        if org is None:
            return
        await set_current_org(session, org.id)
        await run_pipeline(session, org, uuid.UUID(meeting_id))


async def _reap_org(org: Org, session: AsyncSession) -> None:
    cutoff = datetime.now(UTC) - timedelta(minutes=STALE_AFTER_MINUTES)
    rows = (
        (
            await session.execute(
                select(Meeting).where(
                    Meeting.org_id == org.id,
                    Meeting.status.in_(list(WORKER_STATUSES) + [MeetingStatus.QUEUED.value]),
                    Meeting.status_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.status = MeetingStatus.FAILED.value
        row.status_at = datetime.now(UTC)
        row.error_key = "meetings.error.stale"
        logger.warning("meetings: run for %s was claimed by nobody; failing it", row.id)


async def meetings_reap_stale(ctx: dict) -> None:  # noqa: ARG001
    if not await _licensed():
        return
    await run_per_org(_reap_org)


async def _sweep_org(org: Org, session: AsyncSession) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=AUDIO_RETENTION_DAYS)
    rows = (
        (
            await session.execute(
                select(Meeting).where(
                    Meeting.org_id == org.id,
                    Meeting.status == MeetingStatus.DONE.value,
                    Meeting.audio_file_id.isnot(None),
                    Meeting.confirmed_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    context = system_context(org, session)
    for row in rows:
        await drop_audio(context, row)


async def meetings_sweep_audio(ctx: dict) -> None:  # noqa: ARG001
    """Nightly: the recordings past their retention go; the words stay."""
    if not await _licensed():
        return
    await run_per_org(_sweep_org)


__all__ = [
    "AUDIO_RETENTION_DAYS",
    "STALE_AFTER_MINUTES",
    "meetings_process",
    "meetings_reap_stale",
    "meetings_sweep_audio",
    "run_pipeline",
]
