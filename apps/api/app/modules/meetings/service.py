"""Meetings: recorded, folded, transcribed, minuted, filed.

The request half. What runs in the worker (folding the chunks, the provider calls, the draft)
lives in ``jobs.py`` and ``pipeline.py``; this file owns the rows, every edit to the minutes and
the roster, and the writes that put a meeting onto other modules' tables — the **contact moment**
the minutes are filed as (``sync_interaction``, written when the draft lands and rewritten on
every edit), the **task** made of one action item (``create_task_for_item``) and the **hours**
booked for the people at the table (``log_time``) — each **through that module's own service,
as the person acting**: an interaction gets the interactions module's validation, kinds,
activity line and events, a task gets the task module's roster rules and its 422s, and neither
module learns that meetings exist.

There is no confirm step. The minutes are the record from the moment they exist and stay
editable for as long as the meeting does; what used to happen on confirm happens on its own
(the contact moment) or on its own button (a task, the hours).
"""

from __future__ import annotations

import base64
import binascii
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.core.activity import ActivityService
from app.core.ai.audio import MAX_AUDIO_BYTES, decode_clip
from app.core.ai.schemas import TaskParseResult
from app.core.ai.service import AIService, enabled_features
from app.core.directory import labels_for, visible_ids
from app.core.entitlements import OrgPlan, refusal_for, sku_writable
from app.core.jobs import enqueue
from app.core.members import staff_select
from app.core.parent import ensure_parent_in_tenant
from app.core.storage.models import StoredFile
from app.core.storage.service import drop_file
from app.core.storage.system import store_system_file
from app.core.tenancy import RequestContext, TenantScopedRepository
from app.errors import AppError
from app.modules.meetings.minutes import FEATURE
from app.modules.meetings.models import (
    ENTITY_TYPE,
    WORKER_STATUSES,
    Meeting,
    MeetingKind,
    MeetingSource,
    MeetingStatus,
)
from app.modules.meetings.pipeline import CONTENT_TYPES, MAX_RECORDING_BYTES
from app.modules.meetings.schemas import (
    MeetingChunk,
    MeetingCreate,
    MeetingDetail,
    MeetingFinish,
    MeetingList,
    MeetingLogTime,
    MeetingParticipant,
    MeetingRow,
    MeetingStatusRead,
    MeetingTaskCreate,
    MeetingTaskCreated,
    MeetingTaskDraftRequest,
    MeetingTimeEntry,
    MeetingTranscript,
    MeetingUpdate,
    MinutesActionItem,
    MinutesDraft,
    TranscriptSegment,
)
from app.modules.meetings.settings import load_settings

logger = logging.getLogger("schakl.meetings")

#: The chunk rows' marker (``files.content_id``): body content of the meeting, never an
#: attachment, folded and dropped by the worker.
CHUNK_PREFIX = "chunk:"
#: How a meeting's interaction kind is spelled in the interactions module's seeded vocabulary.
INTERACTION_KINDS: dict[str, str] = {
    MeetingKind.PHYSICAL.value: "physical_meeting",
    MeetingKind.ONLINE.value: "online_meeting",
}
#: The definition fields the trail records (§16) — never the transcript or the draft.
_TRACKED = ("title", "kind", "company_id", "project_id", "occurred_at")


def chunk_content_id(seq: int) -> str:
    return f"{CHUNK_PREFIX}{seq:06d}"


def _now() -> datetime:
    return datetime.now(UTC)


class MeetingService:
    class _PortalMeetingRepository(TenantScopedRepository):
        """The repo a portal login gets (#266) — the tasks/invoicing/contacts pattern. It defers
        to ``Meeting.__portal_horizon_clause__`` (nothing), overriding ``company_horizon`` so
        the trash half and every count keep riding in from the base."""

        def company_horizon(self):  # noqa: ANN202 — mirrors the base signature
            return Meeting.__portal_horizon_clause__(self.company_scope)

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = (
            self._PortalMeetingRepository(
                ctx.session, ctx.org.id, Meeting, company_scope=ctx.company_scope
            )
            if ctx.is_portal
            else ctx.repo(Meeting)
        )

    # --- reads ------------------------------------------------------------------------ #
    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        status: str | None = None,
        q: str | None = None,
        count: bool = True,
    ) -> MeetingList:
        self.ctx.require("meetings.meeting.read")
        stmt = self.repo.scoped_select()
        count_stmt = self.repo.scoped_count_select()
        if company_id is not None:
            stmt = stmt.where(Meeting.company_id == company_id)
            count_stmt = count_stmt.where(Meeting.company_id == company_id)
        if project_id is not None:
            stmt = stmt.where(Meeting.project_id == project_id)
            count_stmt = count_stmt.where(Meeting.project_id == project_id)
        if status:
            wanted = [s for s in status.split(",") if s]
            stmt = stmt.where(Meeting.status.in_(wanted))
            count_stmt = count_stmt.where(Meeting.status.in_(wanted))
        if q:
            needle = f"%{q.strip()}%"
            cond = Meeting.title.ilike(needle) | Meeting.transcript_text.ilike(needle)
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        rows = (
            (
                await self.ctx.session.execute(
                    stmt.order_by(Meeting.occurred_at.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        total = int(await self.ctx.session.scalar(count_stmt) or 0) if count else None
        return MeetingList(items=await self._rows(list(rows)), total=total)

    async def _rows(self, rows: list[Meeting]) -> list[MeetingRow]:
        """The list shape, with the client and project *named* through the directory seam —
        batched, and only the ones this caller may see."""
        companies = await labels_for(self.ctx, "company", [r.company_id for r in rows])
        projects = await labels_for(self.ctx, "project", [r.project_id for r in rows])
        out: list[MeetingRow] = []
        for row in rows:
            payload = MeetingRow.model_validate(row)
            payload.company_name = companies.get(row.company_id) if row.company_id else None
            payload.project_name = projects.get(row.project_id) if row.project_id else None
            minutes = row.minutes or {}
            payload.action_item_count = len(minutes.get("action_items") or [])
            payload.decision_count = len(minutes.get("decisions") or [])
            out.append(payload)
        return out

    async def get(self, meeting_id: uuid.UUID) -> MeetingDetail:
        self.ctx.require("meetings.meeting.read")
        row = await self.repo.get_or_404(meeting_id)
        return await self._detail(row)

    async def _detail(self, row: Meeting) -> MeetingDetail:
        base = (await self._rows([row]))[0]
        transcript = row.transcript or {}
        segments = [
            TranscriptSegment(
                start=float(s.get("start") or 0),
                end=float(s.get("end") or 0),
                speaker=s.get("speaker"),
                text=str(s.get("text") or ""),
            )
            for s in (transcript.get("segments") or [])
            if isinstance(s, dict)
        ]
        minutes = None
        if row.minutes:
            try:
                minutes = MinutesDraft.model_validate(row.minutes)
            except ValueError:  # a draft stored by an older shape: the screen says "none"
                minutes = None
        participants = participants_of(row)
        settings = await load_settings(self.ctx.session, self.ctx.org.id)
        return MeetingDetail(
            **base.model_dump(),
            language=row.language,
            participants_informed_at=row.participants_informed_at,
            chunks_received=row.chunks_received,
            updated_at=row.updated_at,
            audio_file_id=row.audio_file_id,
            audio_content_type=(
                CONTENT_TYPES.get(row.audio_format or "") if row.audio_file_id else None
            ),
            segments=segments,
            transcript_text=row.transcript_text,
            transcript_model=transcript.get("model"),
            transcript_parts=int(transcript.get("parts") or 0),
            transcript_aligned=bool(transcript.get("aligned")),
            diarized=any(s.speaker for s in segments),
            participants=participants,
            speakers=speaker_names(participants),
            minutes=minutes,
            interaction_id=row.interaction_id,
            task_ids=[uuid.UUID(str(t)) for t in (row.task_ids or [])],
            time_entries=await self._time_entries(row, participants),
            can_write=self.ctx.can("meetings.meeting.write"),
            can_delete=self.ctx.can("meetings.meeting.delete"),
            can_create_task=self.ctx.can("tasks.task.create") and not self.ctx.is_portal,
            can_log_time_own=self.ctx.can("time.entry.write"),
            can_log_time_any=self.ctx.can("time.entry.write", "any"),
            document_sections=document_sections_for(row, settings.document_sections),
        )

    async def _time_entries(
        self, row: Meeting, participants: list[MeetingParticipant]
    ) -> list[MeetingTimeEntry]:
        """The hours booked for this meeting, read back through the time module's published
        surface and named through the roster (a booked colleague is a participant by
        construction)."""
        ids = [uuid.UUID(str(t)) for t in (row.time_entry_ids or [])]
        if not ids:
            return []
        from app.modules.time import system as time_system

        names = {p.user_id: p.name for p in participants if p.user_id is not None}
        rows = await time_system.entries_by_ids(self.ctx, ids)
        missing = [r["user_id"] for r in rows if r["user_id"] not in names]
        if missing:
            from app.core.auth.models import User

            found = await self.ctx.session.execute(
                staff_select(self.ctx.org.id).where(User.id.in_(missing))
            )
            for user in found.scalars():
                names[user.id] = user.full_name or user.email
        by_id = {r["id"]: r for r in rows}
        return [
            MeetingTimeEntry(
                id=r["id"],
                user_id=r["user_id"],
                user_name=names.get(r["user_id"], "?"),
                minutes=r["minutes"],
                date=r["started_at"].date(),
            )
            for r in (by_id[i] for i in ids if i in by_id)
        ]

    async def transcript(self, meeting_id: uuid.UUID) -> MeetingTranscript:
        """The words whole, every label resolved to a name — one shape for an agent and for
        the four file formats (``transcript.py``)."""
        self.ctx.require("meetings.meeting.read")
        row = await self.repo.get_or_404(meeting_id)
        detail = await self._detail(row)
        return MeetingTranscript(
            meeting_id=row.id,
            title=row.title,
            occurred_at=row.occurred_at,
            language=row.language,
            model=detail.transcript_model,
            parts=detail.transcript_parts,
            diarized=detail.diarized,
            speakers=detail.speakers,
            segments=detail.segments,
            text=row.transcript_text or "",
        )

    async def status(self, meeting_id: uuid.UUID) -> MeetingStatusRead:
        """One row, three columns — what the detail page polls while a worker holds it."""
        self.ctx.require("meetings.meeting.read")
        row = (
            await self.ctx.session.execute(
                self.repo.scoped_select()
                .with_only_columns(Meeting.status, Meeting.error_key, Meeting.status_at)
                .where(Meeting.id == meeting_id)
            )
        ).first()
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return MeetingStatusRead(status=row[0], error_key=row[1], status_at=row[2])

    # --- writes ------------------------------------------------------------------------ #
    async def _require_feature(self) -> None:
        """Recording is only offered where the org can transcribe: a meeting nobody can turn
        into words is a file, and the screen already hides the button (off means invisible)."""
        if FEATURE not in await enabled_features(self.ctx.session, self.ctx.org.id):
            raise AppError("ai_feature_disabled", "errors.ai_feature_disabled", status_code=409)

    async def create(self, data: MeetingCreate) -> MeetingDetail:
        self.ctx.require("meetings.meeting.write")
        if self.ctx.is_portal:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        await self._require_feature()
        # The statement is asked for unless the org switched it off (Instellingen →
        # Vergaderingen): an agency whose own procedure covers it drops the checkbox and the
        # refusal together, never one without the other.
        settings = await load_settings(self.ctx.session, self.ctx.org.id)
        if settings.consent_required and not data.participants_informed:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"participants_informed": "meetings.error.participants_informed"},
            )
        for fk, table in (("company_id", "companies"), ("project_id", "projects")):
            await ensure_parent_in_tenant(
                self.ctx.session, table, getattr(data, fk), self.ctx.org.id
            )
        user = self.ctx.user
        participants = await self._clean_participants(data.participants)
        if not any(p.user_id == user.id for p in participants):
            # The person pressing record is in the room by definition.
            participants.insert(
                0, MeetingParticipant(name=user.full_name or user.email, user_id=user.id)
            )
        now = _now()
        title = (data.title or "").strip()
        title_auto = not title
        if title_auto:
            # Nobody typed one: name it after the kind, the client and the day, and say so on
            # the row so the minutes may name it properly once the words are in.
            title = await self._auto_title(
                data.kind.value, data.company_id, data.occurred_at or now
            )
        row = await self.repo.create(
            title=title,
            title_auto=title_auto,
            kind=data.kind.value,
            source=data.source.value,
            status=MeetingStatus.RECORDING.value,
            status_at=now,
            occurred_at=data.occurred_at or now,
            language=(data.language or self.ctx.user.locale or "").split("-")[0][:10] or None,
            company_id=data.company_id,
            project_id=data.project_id,
            owner_user_id=user.id,
            owner_name=user.full_name or user.email,
            participants_informed_at=now if data.participants_informed else None,
            participants=[p.model_dump(mode="json") for p in participants],
        )
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, row.id)
        return await self._detail(row)

    async def _auto_title(
        self, kind: str, company_id: uuid.UUID | None, occurred_at: datetime
    ) -> str:
        """"Bespreking met Nova Fietsen · 23-09-2026" — the org's language, the org's calendar
        (a meeting recorded at 00:30 is dated the day the people in it would say)."""
        from app.core.timezone import org_zoneinfo
        from app.i18n import translate

        locale = await org_locale(self.ctx)
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        day = occurred_at.astimezone(zone).strftime("%d-%m-%Y")
        kind_label = translate(f"meetings.kind.{kind}", locale)
        company = None
        if company_id is not None:
            company = (await labels_for(self.ctx, "company", [company_id])).get(company_id)
        if company:
            return translate(
                "meetings.title.auto_with_client", locale, kind=kind_label, client=company, date=day
            )[:255]
        return translate("meetings.title.auto", locale, kind=kind_label, date=day)[:255]

    async def _clean_participants(
        self, participants: list[MeetingParticipant]
    ) -> list[MeetingParticipant]:
        """The roster as it will be stored: every contact one this caller may see, every
        colleague one of the org's staff, every speaker label used at most once.

        The contact check goes through the directory seam (``visible_ids``) for §15's reason: a
        contact's client lives in ``company_contacts``, a table this module may not know about,
        so "belongs to this org" would let a company-group-scoped member minute a meeting with a
        person at a client they cannot see.
        """
        cleaned: list[MeetingParticipant] = []
        labels: set[str] = set()
        contact_ids = [p.contact_id for p in participants if p.contact_id is not None]
        user_ids = [p.user_id for p in participants if p.user_id is not None]
        visible_contacts: set[uuid.UUID] = set()
        if contact_ids:
            visible_contacts = await visible_ids(self.ctx, "contact", contact_ids)
        staff: set[uuid.UUID] = set()
        if user_ids:
            from app.core.auth.models import User

            rows = await self.ctx.session.execute(
                staff_select(self.ctx.org.id).where(User.id.in_(user_ids))
            )
            staff = {u.id for u in rows.scalars()}
        for p in participants:
            name = p.name.strip()[:255]
            if not name:
                continue
            if p.contact_id is not None and p.contact_id not in visible_contacts:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"participants": "errors.not_found"},
                )
            if p.user_id is not None and p.user_id not in staff:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"participants": "errors.not_found"},
                )
            label = (p.speaker or "").strip()[:20] or None
            if label is not None:
                if label in labels:
                    raise AppError(
                        "validation",
                        "errors.validation",
                        status_code=422,
                        fields={"participants": "meetings.error.speaker_twice"},
                    )
                labels.add(label)
            cleaned.append(
                MeetingParticipant(
                    name=name, user_id=p.user_id, contact_id=p.contact_id, speaker=label
                )
            )
        return cleaned

    async def _writable(self, meeting_id: uuid.UUID) -> Meeting:
        self.ctx.require("meetings.meeting.write")
        return await self.repo.get_or_404(meeting_id)

    async def update(self, meeting_id: uuid.UUID, data: MeetingUpdate) -> MeetingDetail:
        row = await self._writable(meeting_id)
        values = data.model_dump(exclude_unset=True)
        for fk, table in (("company_id", "companies"), ("project_id", "projects")):
            if fk in values:
                await ensure_parent_in_tenant(self.ctx.session, table, values[fk], self.ctx.org.id)
        if "kind" in values and values["kind"] is not None:
            values["kind"] = values["kind"].value
        if "title" in values:
            values["title"] = (values["title"] or "").strip() or row.title
            if values["title"] != row.title:
                values["title_auto"] = False
        before = {f: getattr(row, f) for f in _TRACKED}
        row = await self.repo.update(row, **values)
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, row.id, before, {f: getattr(row, f) for f in _TRACKED}
        )
        # The moment follows the filing: a meeting moved to another client moves its timeline
        # entry with it, and a retitled one is retitled there too.
        await self.sync_interaction(row)
        return await self._detail(row)

    async def add_chunk(self, meeting_id: uuid.UUID, data: MeetingChunk) -> int:
        """Store one piece of the recording; returns how many the row now holds.

        Only the first piece has a container header, so only it is sniffed (``decode_clip``)
        — a later one is a continuation and would fail the magic-number check by construction.
        Every piece is held to the dictation's byte cap: a recorder sends one a minute, an
        upload cuts its file into a few megabytes each, and 24 MiB in one piece is neither.

        Every piece is also the recording saying it is still alive: it stamps ``status_at``,
        which is how the server decides a recorder is gone and ends the recording itself
        (``jobs._reap_recordings``).
        """
        row = await self._writable(meeting_id)
        if row.status != MeetingStatus.RECORDING.value:
            raise AppError("conflict", "meetings.error.not_recording", status_code=409)
        if data.seq == 0:
            clip = decode_clip(data.audio)
            payload, extension = clip.data, clip.extension
        else:
            if row.audio_format is None:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"seq": "meetings.error.first_chunk_missing"},
                )
            try:
                payload = base64.b64decode(data.audio, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise AppError("validation", "errors.validation", status_code=422) from exc
            if not payload or len(payload) > MAX_AUDIO_BYTES:
                raise AppError("validation", "errors.ai_audio_too_large", status_code=413)
            extension = row.audio_format
        content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
        # Idempotent per sequence number: a retried upload of the same piece replaces it
        # rather than doubling a minute of the meeting.
        existing = await self.ctx.session.scalar(
            select(StoredFile).where(
                StoredFile.org_id == self.ctx.org.id,
                StoredFile.entity_type == ENTITY_TYPE,
                StoredFile.entity_id == row.id,
                StoredFile.content_id == chunk_content_id(data.seq),
            )
        )
        if existing is not None:
            await drop_file(self.ctx, existing)
        stored = await store_system_file(
            self.ctx,
            filename=f"chunk-{data.seq:06d}.{extension}",
            content_type=content_type,
            data=payload,
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            content_id=chunk_content_id(data.seq),
            created_by_user_id=self.ctx.user.id,
            max_bytes=MAX_AUDIO_BYTES,
            allowed_types=frozenset(CONTENT_TYPES.values()),
        )
        if stored is None:  # pragma: no cover - the caps above already refused
            raise AppError("validation", "errors.validation", status_code=422)
        received = int(
            await self.ctx.session.scalar(
                select(func.count())
                .select_from(StoredFile)
                .where(
                    StoredFile.org_id == self.ctx.org.id,
                    StoredFile.entity_type == ENTITY_TYPE,
                    StoredFile.entity_id == row.id,
                    StoredFile.content_id.like(f"{CHUNK_PREFIX}%"),
                )
            )
            or 0
        )
        # ``status_at`` is stamped with every piece, so "when did this recording last say
        # anything" is one column. The reaper reads it (``jobs._reap_recordings``) to decide
        # that a recorder is gone, and only a piece may make a recording look alive — a title
        # edited mid-recording bumps ``updated_at`` and must not buy a dead tab another hour.
        values: dict[str, Any] = {"chunks_received": received, "status_at": _now()}
        if data.seq == 0:
            values["audio_format"] = extension
        await self.repo.update(row, **values)
        return received

    async def finish(self, meeting_id: uuid.UUID, data: MeetingFinish) -> MeetingDetail:
        """The recorder stopped: hand the pieces to the worker."""
        row = await self._writable(meeting_id)
        if row.status != MeetingStatus.RECORDING.value:
            raise AppError("conflict", "meetings.error.not_recording", status_code=409)
        if row.chunks_received == 0:
            raise AppError("validation", "meetings.error.no_audio", status_code=422)
        await self.repo.update(
            row,
            status=MeetingStatus.QUEUED.value,
            status_at=_now(),
            error_key=None,
            duration_seconds=data.duration_seconds,
        )
        await ActivityService(self.ctx).record(ENTITY_TYPE, row.id, "meeting.recorded", {})
        await self._enqueue(row)
        return await self._detail(row)

    async def retry(self, meeting_id: uuid.UUID) -> MeetingDetail:
        """Run the pipeline again over what is stored — after a provider outage, a budget
        top-up, or a change of speech provider."""
        row = await self._writable(meeting_id)
        if row.status not in (MeetingStatus.FAILED.value, MeetingStatus.READY.value):
            raise AppError("conflict", "meetings.error.not_retryable", status_code=409)
        if row.audio_file_id is None and row.chunks_received == 0:
            raise AppError("validation", "meetings.error.no_audio", status_code=422)
        await self._require_feature()
        await self.repo.update(
            row, status=MeetingStatus.QUEUED.value, status_at=_now(), error_key=None
        )
        await self._enqueue(row)
        return await self._detail(row)

    async def _enqueue(self, row: Meeting, *, stage: str = "full") -> None:
        # A fresh id per queue: arq declines a job whose *result* is still in Redis, and a
        # retry an hour after the first run would otherwise queue nothing and sit on
        # ``queued`` until the reaper called it failed (``core.jobs.enqueue``'s own warning).
        job = await enqueue(
            "meetings_process",
            str(self.ctx.org.id),
            str(row.id),
            stage,
            _job_id=f"meetings-process-{row.id}-{int(_now().timestamp())}",
        )
        if job is None:
            await self.repo.update(
                row, status=MeetingStatus.FAILED.value, error_key="meetings.error.not_queued"
            )

    async def set_participants(
        self, meeting_id: uuid.UUID, participants: list[MeetingParticipant]
    ) -> MeetingDetail:
        """The whole roster, replaced — who was there and which label each one speaks under."""
        row = await self._writable(meeting_id)
        cleaned = await self._clean_participants(participants)
        row = await self.repo.update(row, participants=[p.model_dump(mode="json") for p in cleaned])
        # The client's contacts on the roster are the contact moment's roster too.
        await self.sync_interaction(row)
        return await self._detail(row)

    async def redraft(self, meeting_id: uuid.UUID) -> MeetingDetail:
        """Write the minutes again over the transcript already here — after the speakers were
        named, which is what lets the draft say *who* took each item on. No new transcription,
        so no new audio cost; the words are the words."""
        row = await self._writable(meeting_id)
        if row.status not in (MeetingStatus.FAILED.value, MeetingStatus.READY.value):
            raise AppError("conflict", "meetings.error.not_retryable", status_code=409)
        if not (row.transcript_text or "").strip():
            raise AppError("validation", "meetings.error.no_transcript", status_code=422)
        await self._require_feature()
        await self.repo.update(
            row, status=MeetingStatus.QUEUED.value, status_at=_now(), error_key=None
        )
        await self._enqueue(row, stage="minutes")
        return await self._detail(row)

    async def save_minutes(self, meeting_id: uuid.UUID, draft: MinutesDraft) -> MeetingDetail:
        """An edit to the minutes — the page autosaves, so this is called often and must stay
        cheap and quiet. A title typed into the minutes is the meeting's title (and clears the
        ✦ schakl put on a generated one); the contact moment is rewritten to match, so the
        timeline never shows words the page no longer does. A task an item already became is
        kept on it whatever the caller sent: the link is a fact, not a field."""
        row = await self._writable(meeting_id)
        if row.status not in (MeetingStatus.READY.value, MeetingStatus.FAILED.value):
            raise AppError("conflict", "meetings.error.not_reviewable", status_code=409)
        if row.minutes:
            # Matched on the words, never on the position: a caller that drops the link and
            # reorders the list would otherwise hand one item another item's task.
            stored = _tasks_by_title(row.minutes)
            for item in draft.action_items:
                if item.task_id is None:
                    item.task_id = stored.get(item.title.strip().casefold())
        values: dict[str, Any] = {"minutes": draft.model_dump(mode="json")}
        title = (draft.title or "").strip()[:255]
        if title and title != row.title:
            values["title"] = title
            values["title_auto"] = False
        before = {f: getattr(row, f) for f in _TRACKED}
        row = await self.repo.update(row, **values)
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, row.id, before, {f: getattr(row, f) for f in _TRACKED}
        )
        await self.sync_interaction(row)
        return await self._detail(row)

    # --- the contact moment ------------------------------------------------------------- #
    async def sync_interaction(self, row: Meeting, *, strict: bool = False) -> uuid.UUID | None:
        """The minutes as a contact moment on the client, made or brought up to date.

        Called by the worker the moment a draft lands (as the recorder, ``jobs._file``), and by
        every edit after that — the minutes, the title, the filing, the roster, a task made of
        an item — so the timeline and the meeting say the same words. The interactions module
        does the writing through its own service: its kinds, its roster rules, its trail. A
        refusal there (the kind deactivated, a permission the caller lacks, the module off) is
        **logged and swallowed** unless ``strict``: the meeting is the record, the moment is a
        mirror of it, and an autosave that failed because the timeline refused would lose the
        edit to save the mirror. The page then shows the moment as not filed and offers the
        strict form (``file_interaction``), whose refusal is the sentence the person needs.
        """
        if not row.minutes:
            return row.interaction_id
        try:
            draft = MinutesDraft.model_validate(row.minutes)
        except ValueError:
            return row.interaction_id
        from app.modules.interactions.schemas import InteractionCreate, InteractionUpdate
        from app.modules.interactions.service import InteractionService

        title = (draft.title or "").strip() or row.title
        locale = await org_locale(self.ctx)
        participants = participants_of(row)
        names = await self._owner_names(draft, participants)
        body = render_minutes(draft, locale=locale, participants=participants, names=names)
        kind = INTERACTION_KINDS.get(row.kind, "physical_meeting")
        # The client's people in the room are the contact moment's roster (#300): a contact's
        # page then lists this meeting under their name, which is the whole point of naming them.
        contact_ids = list(
            dict.fromkeys(p.contact_id for p in participants if p.contact_id is not None)
        )
        task_ids = [uuid.UUID(str(t)) for t in (row.task_ids or [])]
        try:
            if row.interaction_id is None:
                created = await InteractionService(self.ctx).create(
                    InteractionCreate(
                        kind=kind,
                        occurred_at=row.occurred_at,
                        subject=title[:500],
                        body_text=body,
                        company_id=row.company_id,
                        project_id=row.project_id,
                        contact_ids=contact_ids or None,
                        task_ids=task_ids or None,
                    )
                )
                interaction_id = uuid.UUID(str(created["id"]))
                await self.repo.update(row, interaction_id=interaction_id)
                await ActivityService(self.ctx).record(
                    ENTITY_TYPE, row.id, "meeting.filed", {"interaction_id": str(interaction_id)}
                )
            else:
                await InteractionService(self.ctx).update(
                    row.interaction_id,
                    InteractionUpdate(
                        kind=kind,
                        occurred_at=row.occurred_at,
                        subject=title[:500],
                        body_text=body,
                        company_id=row.company_id,
                        project_id=row.project_id,
                        contact_ids=contact_ids,
                        task_ids=task_ids,
                    ),
                )
        except AppError as exc:
            if strict:
                raise
            logger.warning(
                "meetings: %s could not be filed as a contact moment: %s", row.id, exc.message_key
            )
            return None
        return row.interaction_id

    async def file_interaction(self, meeting_id: uuid.UUID) -> MeetingDetail:
        """The page's button for a meeting the automatic filing skipped — a row on ``review``
        from before the confirm step was removed, or one whose filing was refused. Raises what
        the interactions module refuses on, so the person reads the reason."""
        row = await self._writable(meeting_id)
        if not row.minutes:
            raise AppError("validation", "meetings.error.no_minutes", status_code=422)
        await self.sync_interaction(row, strict=True)
        return await self._detail(row)

    # --- the hours ----------------------------------------------------------------------- #
    async def log_time(self, meeting_id: uuid.UUID, data: MeetingLogTime) -> MeetingDetail:
        """One time entry per colleague named, for the meeting's length, filed on the contact
        moment — the "Uren registreren" button. Every gate is asked before anything is written
        (``_log_time_plan``), and a meeting not yet filed is filed first so the entries have a
        moment to hang on; where that filing is refused the hours still land, unfiled."""
        row = await self._writable(meeting_id)
        if row.status in WORKER_STATUSES or row.status in (
            MeetingStatus.RECORDING.value,
            MeetingStatus.QUEUED.value,
        ):
            raise AppError("conflict", "meetings.error.busy", status_code=409)
        plan = await self._log_time_plan(row, data)
        interaction_id = await self.sync_interaction(row)
        draft = MinutesDraft.model_validate(row.minutes) if row.minutes else MinutesDraft()
        entry_ids = await self._log_time(row, interaction_id, plan, draft, row.title)
        row = await self.repo.update(
            row,
            time_entry_ids=[str(t) for t in (row.time_entry_ids or [])]
            + [str(t) for t in entry_ids],
        )
        await ActivityService(self.ctx).record(
            ENTITY_TYPE, row.id, "meeting.hours_logged", {"time_entries": len(entry_ids)}
        )
        return await self._detail(row)

    async def _log_time_plan(
        self, row: Meeting, log_time: MeetingLogTime | None
    ) -> tuple[list[uuid.UUID], int, MeetingLogTime] | None:
        """Everything the hours need, checked before anything is written (#314's three gates,
        and a fourth): ``time.entry.write`` — at ``:any`` for anyone but the caller, since the
        entry lands on *their* timesheet; the ``time`` sku still writable, because this route
        rides ``meetings``' licence gate and a ride-along must never be the one way an
        uncovered module can still be written to (§18); every id one of the org's staff; and a
        duration, the recording's own where none was typed — a meeting with neither is a
        request nobody can honour, refused with the field named."""
        if log_time is None:
            return None
        if not await sku_writable("time", plan=OrgPlan.of(self.ctx.org)):
            raise AppError(*refusal_for("time"), status_code=402)
        user_ids = list(dict.fromkeys(log_time.user_ids))
        if any(uid != self.ctx.user.id for uid in user_ids):
            self.ctx.require("time.entry.write", "any")
        else:
            self.ctx.require("time.entry.write")
        from app.core.auth.models import User

        rows = await self.ctx.session.execute(
            staff_select(self.ctx.org.id).where(User.id.in_(user_ids))
        )
        staff = {u.id for u in rows.scalars()}
        if any(uid not in staff for uid in user_ids):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"log_time": "errors.not_found"},
            )
        minutes = log_time.minutes
        if minutes is None and row.duration_seconds:
            minutes = max(1, int((row.duration_seconds + 59) // 60))
        if not minutes:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"log_time": "meetings.error.log_time_minutes"},
            )
        return user_ids, minutes, log_time

    async def _log_time(
        self,
        row: Meeting,
        interaction_id: uuid.UUID | None,
        plan: tuple[list[uuid.UUID], int, MeetingLogTime] | None,
        draft: MinutesDraft,
        title: str,
    ) -> list[uuid.UUID]:
        """One entry per colleague named, for the meeting's length, filed on the contact moment
        where there is one — through the time module's published surface (§6), typed after the
        moment's kind exactly as a hand-logged call is (#182)."""
        if plan is None:
            return []
        user_ids, minutes, log_time = plan
        from datetime import timedelta

        from app.modules.interactions import system as interactions_system
        from app.modules.time import system as time_system

        kind = INTERACTION_KINDS.get(row.kind, "physical_meeting")
        entry_type_key = await time_system.ensure_type_for_kind(
            self.ctx, kind, await interactions_system.kind_label(self.ctx, kind)
        )
        description = (
            (log_time.description or "").strip() or (draft.time_note or "").strip() or title
        )
        ids: list[uuid.UUID] = []
        for uid in user_ids:
            entry = await time_system.record_entry(
                self.ctx,
                user_id=uid,
                started_at=row.occurred_at,
                ended_at=row.occurred_at + timedelta(minutes=minutes),
                company_id=row.company_id,
                project_id=row.project_id,
                description=description[:2000],
                entry_type_key=entry_type_key,
                interaction_id=interaction_id,
                billable=log_time.billable,
            )
            ids.append(entry.id)
        return ids

    # --- a task from one action item, drafted by schakl --------------------------- #
    async def _item_at(self, row: Meeting, index: int) -> tuple[MinutesDraft, MinutesActionItem]:
        if not row.minutes:
            raise AppError("validation", "meetings.error.no_minutes", status_code=422)
        try:
            draft = MinutesDraft.model_validate(row.minutes)
        except ValueError as exc:
            raise AppError("validation", "meetings.error.no_minutes", status_code=422) from exc
        if index >= len(draft.action_items):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return draft, draft.action_items[index]

    async def draft_task(
        self, meeting_id: uuid.UUID, data: MeetingTaskDraftRequest
    ) -> TaskParseResult:
        """Schakl fills the task form for one action item of the stored minutes (the e-mail
        approve's "laat schakl deze taak invullen", on the review desk). Creates nothing."""
        from app.modules.meetings.taskdraft import draft_task_for_item

        row = await self._writable(meeting_id)
        self.ctx.require("tasks.task.create")
        await self._require_feature()
        draft, item = await self._item_at(row, data.index)
        participants = participants_of(row)
        names = await self._owner_names(draft, participants)
        owner = names.get(_owner_key(item) or "") or item.owner_label
        company = None
        if row.company_id is not None:
            company = (await labels_for(self.ctx, "company", [row.company_id])).get(row.company_id)
        return await draft_task_for_item(
            AIService(self.ctx),
            row=row,
            item=item,
            participants=participants,
            agency=await agency_name(self.ctx.session, self.ctx.org),
            locale=await org_locale(self.ctx),
            owner_name=owner,
            company_name=company,
            override_budget=data.override_budget,
        )

    async def create_task_for_item(
        self, meeting_id: uuid.UUID, data: MeetingTaskCreate
    ) -> MeetingTaskCreated:
        """The reviewed draft becomes a task — the meeting's client's, through the tasks
        module's own service as the reviewer — the action item remembers it, and the contact
        moment lists it. Once per item: the link is what stops a second press."""
        from app.modules.tasks.schemas import TaskCreate, TaskCreateChecklist
        from app.modules.tasks.service import TaskService

        row = await self._writable(meeting_id)
        if row.status not in (MeetingStatus.READY.value, MeetingStatus.FAILED.value):
            raise AppError("conflict", "meetings.error.not_reviewable", status_code=409)
        draft, item = await self._item_at(row, data.index)
        if item.task_id is not None:
            raise AppError("conflict", "meetings.error.task_exists", status_code=409)
        if data.project_id is not None:
            await ensure_parent_in_tenant(
                self.ctx.session, "projects", data.project_id, self.ctx.org.id
            )
        locale = await org_locale(self.ctx)
        task = await TaskService(self.ctx).create(
            TaskCreate(
                title=data.title,
                description=_task_notes(item, row.title, locale, description=data.description),
                due_date=data.due_date,
                priority=data.priority or "normal",
                status=data.status,
                company_id=row.company_id,
                project_id=data.project_id or row.project_id,
                assignee_user_id=None if data.assignee_contact_id else data.assignee_user_id,
                assignee_contact_id=data.assignee_contact_id,
                allocated_minutes=data.allocated_minutes,
                requires_interaction=data.requires_interaction,
                visible_to_client=data.visible_to_client,
                checklist=(
                    TaskCreateChecklist(
                        title=data.checklist_title,
                        items=[i.model_dump() for i in data.checklist_items],
                    )
                    if data.checklist_items or data.checklist_title
                    else None
                ),
                links=[link.model_dump() for link in data.links],
                label_ids=data.label_ids,
            )
        )
        item.task_id = task.id
        item.title = data.title
        item.due_date = data.due_date
        task_ids = [uuid.UUID(str(t)) for t in (row.task_ids or [])] + [task.id]
        row = await self.repo.update(
            row, minutes=draft.model_dump(mode="json"), task_ids=[str(t) for t in task_ids]
        )
        await ActivityService(self.ctx).record(
            ENTITY_TYPE, row.id, "meeting.task_created", {"task_id": str(task.id)}
        )
        # Filed on the contact moment (its task roster, #300) — made first where none exists.
        await self.sync_interaction(row)
        return MeetingTaskCreated(task_id=task.id, meeting=await self._detail(row))

    async def _owner_names(
        self, draft: MinutesDraft, participants: list[MeetingParticipant]
    ) -> dict[str, str]:
        """``u:<id>`` / ``c:<id>`` → a display name, for every owner the minutes name.

        The roster answers most of them; a colleague the reviewer picked from the whole staff
        list, or a contact not minuted as present, is looked up — the contact through the
        directory seam, so a name this caller may not see is not printed either.
        """
        names: dict[str, str] = {}
        for p in participants:
            if p.user_id is not None:
                names.setdefault(f"u:{p.user_id}", p.name)
            if p.contact_id is not None:
                names.setdefault(f"c:{p.contact_id}", p.name)
        missing_users = {
            i.assignee_user_id
            for i in draft.action_items
            if i.assignee_user_id is not None and f"u:{i.assignee_user_id}" not in names
        }
        if missing_users:
            from app.core.auth.models import User

            rows = await self.ctx.session.execute(
                staff_select(self.ctx.org.id).where(User.id.in_(list(missing_users)))
            )
            for user in rows.scalars():
                names[f"u:{user.id}"] = user.full_name or user.email
        missing_contacts = {
            i.owner_contact_id
            for i in draft.action_items
            if i.owner_contact_id is not None and f"c:{i.owner_contact_id}" not in names
        }
        if missing_contacts:
            for cid, label in (await labels_for(self.ctx, "contact", missing_contacts)).items():
                names[f"c:{cid}"] = label
        return names

    async def delete_audio(self, meeting_id: uuid.UUID) -> MeetingDetail:
        """Drop the recording, keep the words — the retention cron's act, on demand."""
        row = await self._writable(meeting_id)
        if row.status in WORKER_STATUSES or row.status in (
            MeetingStatus.RECORDING.value,
            MeetingStatus.QUEUED.value,
        ):
            raise AppError("conflict", "meetings.error.busy", status_code=409)
        await drop_audio(self.ctx, row)
        return await self._detail(row)

    async def delete(self, meeting_id: uuid.UUID) -> None:
        """Everything: the audio, the pieces, the words, the draft. The contact moment and the
        tasks a confirm produced are records of their own modules and stay."""
        self.ctx.require("meetings.meeting.delete")
        row = await self.repo.get_or_404(meeting_id)
        if row.status in WORKER_STATUSES:
            raise AppError("conflict", "meetings.error.busy", status_code=409)
        await drop_audio(self.ctx, row)
        await ActivityService(self.ctx).record(ENTITY_TYPE, row.id, "deleted", {"title": row.title})
        await self.repo.delete(row)


async def _org_today(ctx: RequestContext):  # noqa: ANN202
    from app.core.timezone import org_today

    return await org_today(ctx.session, ctx.org.id)


async def agency_name(session: Any, org: Any) -> str:
    """The name the minutes and the task drafts speak of — the brand, else the org."""
    from app.core.models import OrgSettings

    brand = await session.scalar(select(OrgSettings.brand_name).where(OrgSettings.org_id == org.id))
    return brand or org.name or "the agency"


async def org_locale(ctx: Any) -> str:
    """The org's own language — the minutes are the agency's document, not the reviewer's UI."""
    from app.config import settings as app_settings
    from app.core.models import OrgSettings

    locale = await ctx.session.scalar(
        select(OrgSettings.default_locale).where(OrgSettings.org_id == ctx.org.id)
    )
    return locale or app_settings.default_locale


def _task_notes(
    item: Any, meeting_title: str, locale: str, *, description: str | None = None
) -> str | None:
    """The task's notes: the item's own (or the reviewed draft's), plus the sentence it rests
    on and where it was said — the evidence travels with the work."""
    from app.i18n import translate

    parts: list[str] = []
    notes = description if description is not None else item.description
    if notes and notes.strip():
        parts.append(notes.strip())
    if item.quote:
        at = f" ({_clock(item.at)})" if item.at is not None else ""
        parts.append(f"> {item.quote.strip()}{at}")
    parts.append(translate("meetings.minutes.from_meeting", locale, title=meeting_title))
    return "\n\n".join(parts) or None


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return ""
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _tasks_by_title(minutes: dict[str, Any]) -> dict[str, uuid.UUID]:
    """``action item title → task id`` as stored, for the links a saved draft must keep."""
    out: dict[str, uuid.UUID] = {}
    for raw in minutes.get("action_items") or []:
        if isinstance(raw, dict) and raw.get("task_id") and raw.get("title"):
            try:
                out[str(raw["title"]).strip().casefold()] = uuid.UUID(str(raw["task_id"]))
            except ValueError:
                continue
    return out


def participants_of(row: Meeting) -> list[MeetingParticipant]:
    """The roster, whichever shape the row was written in.

    ``participants`` where it exists; else the first shape, ``speakers = {"S1": "Jan"}``, read
    as one name-only participant per label. A row nobody has touched since the column shipped
    therefore reads exactly as it did.
    """
    if row.participants is not None:
        out: list[MeetingParticipant] = []
        for raw in row.participants:
            if not isinstance(raw, dict):
                continue
            try:
                out.append(MeetingParticipant.model_validate(raw))
            except ValueError:
                continue
        return out
    return [
        MeetingParticipant(name=str(name).strip(), speaker=str(label)[:20])
        for label, name in (row.speakers or {}).items()
        if str(name).strip()
    ]


def document_sections_for(row: Meeting, defaults: list[str]) -> list[str]:
    """The org's default sections, minus the ones this meeting has nothing for — a ticked box
    for an empty chapter is a control that draws a heading over nothing."""
    minutes = row.minutes or {}
    present = {
        "participants": bool(participants_of(row)),
        "summary": bool((minutes.get("summary") or "").strip()),
        "topics": bool(minutes.get("topics")),
        "decisions": bool(minutes.get("decisions")),
        "action_items": bool(minutes.get("action_items")),
        "open_questions": bool(minutes.get("open_questions")),
        "evidence": bool(minutes.get("decisions") or minutes.get("action_items")),
        "transcript": bool((row.transcript_text or "").strip()),
    }
    return [key for key in defaults if present.get(key, True)]


def speaker_names(participants: list[MeetingParticipant]) -> dict[str, str]:
    """Label → name, the transcript's own lookup."""
    return {p.speaker: p.name for p in participants if p.speaker}


def _owner_key(item: Any) -> str | None:
    if item.owner_contact_id is not None:
        return f"c:{item.owner_contact_id}"
    if item.assignee_user_id is not None:
        return f"u:{item.assignee_user_id}"
    return None


def render_minutes(
    draft: MinutesDraft,
    *,
    locale: str,
    participants: list[MeetingParticipant],
    names: dict[str, str],
) -> str:
    """The minutes as the markdown the contact moment carries — headings in the org's language,
    the reviewer's words verbatim, every claim with its timestamp beside it.

    The action items are written **by side and then by person**: what the agency took on,
    under each colleague; what the client took on, under each contact; and the rest. A reader
    on either side finds their own list without reading the other's, which is what a list of
    action items is for.
    """
    from app.i18n import translate

    lines: list[str] = []
    if participants:
        heading = translate("meetings.minutes.heading_attendees", locale)
        lines += [f"## {heading}", "", ", ".join(p.name for p in participants), ""]
    if draft.summary.strip():
        heading = translate("meetings.minutes.heading_summary", locale)
        lines += [f"## {heading}", "", draft.summary.strip(), ""]
    if draft.topics:
        lines += [f"## {translate('meetings.minutes.heading_topics', locale)}", ""]
        for topic in draft.topics:
            lines += [f"### {topic.heading.strip()}", "", topic.text.strip(), ""]
    if draft.decisions:
        lines += [f"## {translate('meetings.minutes.heading_decisions', locale)}", ""]
        for decision in draft.decisions:
            at = f" _({_clock(decision.at)})_" if decision.at is not None else ""
            lines.append(f"- {decision.text.strip()}{at}")
        lines.append("")
    if draft.action_items:
        lines += [f"## {translate('meetings.minutes.heading_actions', locale)}", ""]
        for side, items in group_action_items(draft.action_items):
            side_heading = translate(f"meetings.minutes.side_{side}", locale)
            lines += [f"### {side_heading}", ""]
            for owner_key, owned in items:
                owner = names.get(owner_key or "", "") if owner_key else ""
                if not owner and owned and owned[0].owner_label:
                    owner = owned[0].owner_label.strip()
                if owner:
                    lines.append(f"**{owner}**")
                for item in owned:
                    due = f" — {item.due_date.isoformat()}" if item.due_date else ""
                    at = f" _({_clock(item.at)})_" if item.at is not None else ""
                    lines.append(f"- {item.title.strip()}{due}{at}")
                lines.append("")
    if draft.open_questions:
        lines += [f"## {translate('meetings.minutes.heading_questions', locale)}", ""]
        lines += [f"- {q.strip()}" for q in draft.open_questions]
        lines.append("")
    return "\n".join(lines).strip()


def group_action_items(
    items: list[Any],
) -> list[tuple[str, list[tuple[str | None, list[Any]]]]]:
    """Action items by side (``agency`` / ``client`` / ``other``), each side by owner, in order
    of first appearance. Items with a free-text owner are grouped on that text; items with
    nobody named share one unnamed group under ``other``."""
    sides: dict[str, dict[str | None, list[Any]]] = {"agency": {}, "client": {}, "other": {}}
    for item in items:
        if item.owner_contact_id is not None:
            side = "client"
        elif item.assignee_user_id is not None:
            side = "agency"
        else:
            side = "other"
        key = _owner_key(item)
        if key is None and item.owner_label and item.owner_label.strip():
            key = f"n:{item.owner_label.strip().lower()}"
        sides[side].setdefault(key, []).append(item)
    return [(side, list(groups.items())) for side, groups in sides.items() if groups]


async def drop_audio(ctx: Any, row: Meeting) -> None:
    """Every stored byte of a meeting: the folded recording and any piece left behind. The row
    keeps its transcript. Shared by the reviewer's button, the delete and the retention cron."""
    files = (
        (
            await ctx.session.execute(
                select(StoredFile).where(
                    StoredFile.org_id == ctx.org.id,
                    StoredFile.entity_type == ENTITY_TYPE,
                    StoredFile.entity_id == row.id,
                )
            )
        )
        .scalars()
        .all()
    )
    for stored in files:
        await drop_file(ctx, stored)
    row.audio_file_id = None
    row.chunks_received = 0
    await ctx.session.flush()


__all__ = [
    "CHUNK_PREFIX",
    "agency_name",
    "INTERACTION_KINDS",
    "MAX_RECORDING_BYTES",
    "MeetingService",
    "MeetingSource",
    "chunk_content_id",
    "document_sections_for",
    "drop_audio",
    "group_action_items",
    "participants_of",
    "render_minutes",
    "speaker_names",
]
