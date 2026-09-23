"""ARQ jobs for meetings: the pipeline run, the reaper, the audio retention sweep.

* ``meetings_process`` — one meeting: fold the pieces into the recording, transcribe it (in as
  many requests as the tenant's speech provider takes, ``pipeline.py``), draft the minutes
  (``minutes.py``). Enqueued by ``finish`` and by ``retry``; with ``stage="minutes"`` (the
  reviewer's *redraft*, after naming the speakers) it starts at the draft over the transcript
  already on the row and spends no audio.
* ``meetings_reap_stale`` — every quarter of an hour, per org. Two kinds of run nobody holds
  any more. A row a worker **claimed and never released** — the process is not there any more —
  is failed, so the screen stops saying "bezig" (the #300 rule, the interactions module's
  shape). And a row still ``recording`` that **nothing has posted a piece to** for
  :data:`RECORDING_STALE_AFTER_MINUTES`: the recorder is a browser tab, and a tab that is
  closed, reloaded, crashed or frozen by a phone locking its screen sends no stop — so the
  server sends it instead. What that means depends on what arrived: pieces were stored, so the
  meeting is *queued* and transcribed from them (the recorder's own "Verwerk wat is opgeslagen",
  taken without a person pressing it); nothing arrived at all, so the row is failed and says
  so. Before this, a ``recording`` row was the one state nothing ever ended: it sat there
  claiming a recording was running, was polled by every page load that opened it, and could be
  cleared only by deleting the meeting.
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
from app.core.events import emit
from app.core.jobs import enqueue, run_per_org, system_context
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
from app.modules.meetings.service import CHUNK_PREFIX, drop_audio, org_locale, participants_of
from app.modules.meetings.settings import load_settings

logger = logging.getLogger("schakl.meetings")

#: A run still claimed after this is claimed by nobody. Long: a three-hour recording through a
#: provider that takes a while is a legitimate forty minutes.
STALE_AFTER_MINUTES = 90
#: A ``recording`` row silent for this long is a recorder that is gone. A tab posts one piece a
#: minute and retries a piece that will not land for ten (``web/.../upload.ts``), so the longest
#: honest silence from a *live* recorder is that budget plus a piece — twenty minutes is past it
#: with room, and the reaper runs four times an hour, so a dead recording is ended inside half
#: an hour rather than never.
RECORDING_STALE_AFTER_MINUTES = 20
#: How long a confirmed meeting keeps its audio. Stated on the recording screen.
AUDIO_RETENTION_DAYS = 30
#: The notification the colleague who recorded it gets when the draft lands on ``review``
#: (registered in ``notifications/events.py``; ``MEETING_READY`` there must match).
READY_EVENT = "meeting.ready"
#: The notification the colleague who recorded it gets when the server had to end a recording
#: that nothing of ever arrived (``notifications/events.py``; ``MEETING_LOST`` must match).
LOST_EVENT = "meeting.lost"


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


async def run_pipeline(
    session: AsyncSession, org: Org, meeting_id: uuid.UUID, *, stage: str = "full"
) -> None:
    """The whole run for one meeting, ending on ``review`` or ``failed``. Never raises.

    ``stage="minutes"`` skips the fold and the transcription and drafts over the transcript the
    row already holds — the reviewer named the speakers and wants the draft to say who took
    what on, and re-transcribing for that would spend audio to answer a question the words
    already answer.
    """
    # Plain values, read once: a rollback on the error path expires every loaded object, and
    # an expired attribute read is a lazy load — sync IO the async session refuses.
    org_id = org.id
    row = await _load(session, org_id, meeting_id)
    if row is None:
        return
    if row.status in WORKER_STATUSES:
        # A run this worker (or its predecessor) claimed and did not finish: the worker rolls
        # stop-first on a redeploy, arq cancels the job and queues it again, and the second run
        # arrives to a row still stamped ``transcribing`` or ``summarising``. Standing down here
        # left the meeting to the reaper — ninety minutes, then ``failed``, then a person pressing
        # retry — for a restart that took ten seconds. So a row in a worker state is *resumed*,
        # and resumed from where the words are: a transcript already committed is not
        # transcribed again (the row was stamped ``summarising`` in the same commit that stored
        # it), because a second transcription is a second bill for the same audio.
        logger.info("meetings: %s was left on %s; resuming", meeting_id, row.status)
        if (row.transcript_text or "").strip():
            stage = "minutes"
    elif row.status != MeetingStatus.QUEUED.value:
        logger.info("meetings: %s is not queued; standing down", meeting_id)
        return
    ctx = system_context(org, session)
    service = AIService(ctx)
    try:
        if stage == "minutes" and (row.transcript_text or "").strip():
            transcript = dict(row.transcript or {})
            segments = [s for s in (transcript.get("segments") or []) if isinstance(s, dict)]
            text = row.transcript_text or ""
            parts = int(transcript.get("parts") or 1)
            speech_model = transcript.get("model")
        else:
            await _set_status(session, org_id, row, MeetingStatus.TRANSCRIBING.value)
            config = await service.speech_config(FEATURE)
            await service.ensure_audio_budget()
            data, extension = await _fold(ctx, session, row)
            language, duration_hint = row.language, row.duration_seconds
            # --- the words: outside any transaction --------------------------------- #
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
            segments, text, parts = transcribed.segments, transcribed.text, transcribed.parts
            speech_model = config.model
            row.transcript = {
                "segments": segments,
                "model": speech_model,
                "parts": parts,
                # Stated on the row: a transcript with no labels is a *model* that answers
                # text only, and the screen names it rather than drawing an empty roster.
                "diarized": any(s.get("speaker") for s in segments),
            }
            row.transcript_text = text or None
            if transcribed.seconds and not row.duration_seconds:
                row.duration_seconds = transcribed.seconds
            await service.record_usage(
                FEATURE, config.model, 0, 0, audio_seconds=transcribed.seconds
            )
        await _set_status(session, org_id, row, MeetingStatus.SUMMARISING.value)
        if not (text or "").strip():
            raise AppError("meetings_no_speech", "meetings.error.no_speech", status_code=422)
        # --- the minutes --------------------------------------------------------------- #
        chat = await service.config_for(FEATURE)
        await service.ensure_budget()
        candidates = await gather_candidates(ctx, "", blocks=frozenset({"members"}))
        zone = await org_zoneinfo(session, org_id)
        today = await org_today(session, org_id)
        locale = await org_locale(ctx)
        house_rules = (await load_settings(session, org_id)).ai_instructions
        draft = await draft_minutes(
            service,
            title=row.title,
            occurred_at=row.occurred_at,
            kind=row.kind,
            segments=list(segments),
            transcript_text=text,
            participants=participants_of(row),
            candidates=candidates,
            today=today,
            now=datetime.now(zone),
            locale=locale,
            agency=await _agency_name(session, org),
            duration=row.duration_seconds,
            house_rules=house_rules,
        )
        # ``complete`` released and re-bound the session; the row object is still ours.
        row = await _load(session, org_id, meeting_id) or row
        row.minutes = draft.model_dump(mode="json")
        row.transcript = {
            "segments": segments,
            "model": speech_model,
            "parts": parts,
            "diarized": any(s.get("speaker") for s in segments),
            "chat_model": chat.model,
        }
        row.status = MeetingStatus.REVIEW.value
        row.status_at = datetime.now(UTC)
        row.error_key = None
        # Told before the commit, so the row's state and the sentence about it land together:
        # the colleague who pressed record is the one waiting, and a worker has no actor to
        # exclude, so they are named outright. Deduped per run, not per meeting — a redraft is
        # a second draft, and hearing it is done is the point of asking for one.
        await _notify_ready(ctx, row, draft)
        await _commit(session, org_id)
    except AppError as exc:
        logger.warning("meetings: %s failed: %s", meeting_id, exc.message_key)
        await _fail(session, org_id, meeting_id, exc.message_key)
    except Exception:
        logger.exception("meetings: pipeline crashed for %s", meeting_id)
        await _fail(session, org_id, meeting_id, "meetings.error.failed")


async def _notify_ready(ctx, row: Meeting, draft) -> None:  # noqa: ANN001
    """The recorder is told their minutes are ready to review — in the app and, by this
    event's own default, by mail (``notifications/defaults.EMAIL_DEFAULT_ON_EVENTS``)."""
    if row.owner_user_id is None:
        return
    await emit(
        READY_EVENT,
        ctx,
        {
            "meeting_id": row.id,
            "title": row.title,
            "decisions": len(draft.decisions),
            "action_items": len(draft.action_items),
            "_recipients": [row.owner_user_id],
            "_dedup_key": f"meeting-ready:{row.id}:{int(row.status_at.timestamp())}",
        },
    )


async def _notify_lost(ctx, row: Meeting) -> None:  # noqa: ANN001
    """The colleague who pressed record is told the recording never arrived.

    Silence is what made this expensive: somebody walked out of a three-hour meeting believing
    it had been recorded and found out hours later, by opening the row. The sentence names the
    meeting and links to it, and mails by this event's own default — the person it is addressed
    to recorded from a phone and is not at their desk (``notifications/defaults``).
    """
    if row.owner_user_id is None:
        return
    await emit(
        LOST_EVENT,
        ctx,
        {
            "meeting_id": row.id,
            "title": row.title,
            "_recipients": [row.owner_user_id],
            "_dedup_key": f"meeting-lost:{row.id}",
        },
    )


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

    brand = await session.scalar(select(OrgSettings.brand_name).where(OrgSettings.org_id == org.id))
    return brand or org.name or "the agency"


async def meetings_process(
    ctx: dict,  # noqa: ARG001
    org_id: str,
    meeting_id: str,
    stage: str = "full",
) -> None:
    if not await _licensed():
        logger.info("meetings: sku not writable; %s not processed", meeting_id)
        return
    async with async_session_maker() as session:
        org = await _active_org(session, org_id)
        if org is None:
            return
        await set_current_org(session, org.id)
        await run_pipeline(session, org, uuid.UUID(meeting_id), stage=stage)


async def _reap_org(org: Org, session: AsyncSession) -> None:
    await _reap_runs(org, session)
    await _reap_recordings(org, session)


async def _reap_runs(org: Org, session: AsyncSession) -> None:
    """A run a worker claimed and never released."""
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


async def _reap_recordings(org: Org, session: AsyncSession) -> None:
    """A recording nothing is feeding any more: end it the way the recorder would have.

    The stop is the one message a dead tab cannot send, and everything else about the recording
    is already on the server — so the server sends it. **Pieces arrived** and the meeting is
    handed to the worker exactly as ``finish`` would (``duration_seconds`` is left alone: the
    recorder's elapsed count died with the tab, and the transcription reports the real length
    anyway). **Nothing arrived** and there is no recording, only a row that says there is one,
    so it is failed with the reason rather than left claiming to be running — a delete would
    also clear the screen and would throw away the title, the client and the roster the person
    typed, which are the half of it that *did* reach us.

    ``status_at`` is what "still going" means here: ``add_chunk`` stamps it with every piece
    (``service.add_chunk``), so a recording that is alive can never look silent, and a title
    edited mid-recording cannot make a dead one look alive.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=RECORDING_STALE_AFTER_MINUTES)
    rows = (
        (
            await session.execute(
                select(Meeting).where(
                    Meeting.org_id == org.id,
                    Meeting.status == MeetingStatus.RECORDING.value,
                    Meeting.status_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    ctx = system_context(org, session)
    for row in rows:
        now = datetime.now(UTC)
        if row.chunks_received <= 0:
            row.status = MeetingStatus.FAILED.value
            row.status_at = now
            row.error_key = "meetings.error.abandoned"
            logger.warning("meetings: recording %s never received a piece; failing it", row.id)
            await _notify_lost(ctx, row)
            continue
        row.status = MeetingStatus.QUEUED.value
        row.status_at = now
        row.error_key = None
        logger.warning(
            "meetings: recording %s went silent with %d pieces; processing them", row.id,
            row.chunks_received,
        )
        # Queued and committed *before* the job is fired, or the worker can pick the row up
        # while this transaction still says ``recording`` and stand down (``run_pipeline``).
        await _commit(session, org.id)
        job = await enqueue(
            "meetings_process",
            str(org.id),
            str(row.id),
            "full",
            _job_id=f"meetings-process-{row.id}-{int(now.timestamp())}",
        )
        if job is None:
            # Nothing is queued, so nothing will move it off ``queued``; say why rather than
            # leaving it for ``_reap_runs`` ninety minutes later (``core.jobs.enqueue``).
            row = await _load(session, org.id, row.id) or row
            row.status = MeetingStatus.FAILED.value
            row.status_at = datetime.now(UTC)
            row.error_key = "meetings.error.not_queued"


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
    "LOST_EVENT",
    "READY_EVENT",
    "RECORDING_STALE_AFTER_MINUTES",
    "STALE_AFTER_MINUTES",
    "meetings_process",
    "meetings_reap_stale",
    "meetings_sweep_audio",
    "run_pipeline",
]
