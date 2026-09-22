"""Meetings: recorded, folded, transcribed, minuted, confirmed.

The request half. What runs in the worker (folding the chunks, the provider calls, the draft)
lives in ``jobs.py`` and ``pipeline.py``; this file owns the rows, the reviewer's edits and
the one write that turns a draft into records — ``confirm``, which lands the minutes as a
contact moment and the ticked action items as tasks, **through those modules' own services, as
the reviewer**: an interaction gets the interactions module's validation, kinds, activity line
and events, a task gets the task module's roster rules and its 422s, and neither module learns
that meetings exist.
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
from app.core.ai.service import enabled_features
from app.core.directory import labels_for, visible_ids
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
    MeetingConfirm,
    MeetingConfirmResult,
    MeetingCreate,
    MeetingDetail,
    MeetingFinish,
    MeetingList,
    MeetingParticipant,
    MeetingRow,
    MeetingStatusRead,
    MeetingUpdate,
    MinutesDraft,
    TranscriptSegment,
)

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
        return MeetingDetail(
            **base.model_dump(),
            language=row.language,
            participants_informed_at=row.participants_informed_at,
            chunks_received=row.chunks_received,
            audio_file_id=row.audio_file_id,
            segments=segments,
            transcript_text=row.transcript_text,
            transcript_model=transcript.get("model"),
            transcript_parts=int(transcript.get("parts") or 0),
            diarized=any(s.speaker for s in segments),
            participants=participants,
            speakers=speaker_names(participants),
            minutes=minutes,
            interaction_id=row.interaction_id,
            task_ids=[uuid.UUID(str(t)) for t in (row.task_ids or [])],
            confirmed_at=row.confirmed_at,
            can_write=self.ctx.can("meetings.meeting.write"),
            can_delete=self.ctx.can("meetings.meeting.delete"),
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
        if not data.participants_informed:
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
        row = await self.repo.create(
            title=data.title.strip(),
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
            participants_informed_at=now,
            participants=[p.model_dump(mode="json") for p in participants],
        )
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, row.id)
        return await self._detail(row)

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
        before = {f: getattr(row, f) for f in _TRACKED}
        row = await self.repo.update(row, **values)
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, row.id, before, {f: getattr(row, f) for f in _TRACKED}
        )
        return await self._detail(row)

    async def add_chunk(self, meeting_id: uuid.UUID, data: MeetingChunk) -> int:
        """Store one piece of the recording; returns how many the row now holds.

        Only the first piece has a container header, so only it is sniffed (``decode_clip``)
        — a later one is a continuation and would fail the magic-number check by construction.
        Every piece is held to the dictation's byte cap: a recorder sends one a minute, an
        upload cuts its file into a few megabytes each, and 24 MiB in one piece is neither.
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
        values: dict[str, Any] = {"chunks_received": received}
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
        if row.status not in (MeetingStatus.FAILED.value, MeetingStatus.REVIEW.value):
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
        return await self._detail(row)

    async def redraft(self, meeting_id: uuid.UUID) -> MeetingDetail:
        """Write the minutes again over the transcript already here — after the speakers were
        named, which is what lets the draft say *who* took each item on. No new transcription,
        so no new audio cost; the words are the words."""
        row = await self._writable(meeting_id)
        if row.status not in (MeetingStatus.FAILED.value, MeetingStatus.REVIEW.value):
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
        """The reviewer's edits, kept as the draft — nothing else moves."""
        row = await self._writable(meeting_id)
        if row.status not in (MeetingStatus.REVIEW.value, MeetingStatus.FAILED.value):
            raise AppError("conflict", "meetings.error.not_reviewable", status_code=409)
        row = await self.repo.update(row, minutes=draft.model_dump(mode="json"))
        return await self._detail(row)

    async def confirm(self, meeting_id: uuid.UUID, data: MeetingConfirm) -> MeetingConfirmResult:
        """The draft becomes records: one contact moment, and a task per ticked action item.

        Each write goes through the owning module's own service as the reviewer, so it meets
        every rule a hand-made one meets and the trail names the person. A task that is refused
        (no client on the meeting, a roster rule) is *reported* on the result rather than
        failing the confirm — the minutes are the record, the tasks are a convenience (§18).
        """
        from app.modules.interactions.schemas import InteractionCreate
        from app.modules.interactions.service import InteractionService
        from app.modules.tasks.schemas import TaskCreate
        from app.modules.tasks.service import TaskService

        row = await self._writable(meeting_id)
        if row.status != MeetingStatus.REVIEW.value:
            raise AppError("conflict", "meetings.error.not_reviewable", status_code=409)
        draft = data.minutes
        title = (draft.title or "").strip() or row.title
        locale = await org_locale(self.ctx)
        participants = participants_of(row)
        names = await self._owner_names(draft, participants)
        body = render_minutes(draft, locale=locale, participants=participants, names=names)
        kind = data.interaction_kind or INTERACTION_KINDS.get(row.kind, "physical_meeting")
        # The client's people in the room are the contact moment's roster (#300): a contact's
        # page then lists this meeting under their name, which is the whole point of naming them.
        contact_ids = list(
            dict.fromkeys(p.contact_id for p in participants if p.contact_id is not None)
        )
        interaction = await InteractionService(self.ctx).create(
            InteractionCreate(
                kind=kind,
                occurred_at=row.occurred_at,
                subject=title[:500],
                body_text=body,
                company_id=row.company_id,
                project_id=row.project_id,
                contact_ids=contact_ids or None,
            )
        )
        interaction_id = uuid.UUID(str(interaction["id"]))

        tasks = TaskService(self.ctx)
        task_ids: list[uuid.UUID] = []
        skipped: list[dict[str, Any]] = []
        # No SAVEPOINT around the create, on purpose. Assigning a task to a contact queues the
        # contact's mail inside ``release_db`` (tasks #454), whose commit is the outermost one
        # in SQLAlchemy 2 — inside ``begin_nested`` it committed the transaction, dropped the
        # RLS GUC, and the very next read here answered 404 (docs: §18's per-row savepoint and
        # §11's ``release_db`` cannot both hold; ``release_db`` wins). Every refusal the tasks
        # module speaks in ``AppError`` is raised before it writes, so catching it without a
        # savepoint still leaves the session usable; a task that lands is committed there and
        # then, which is the per-row durability the savepoint was for.
        company_id, project_id = row.company_id, row.project_id
        for item in draft.action_items:
            if not item.create_task:
                continue
            try:
                # A contact who took it on gets the task *as the client's* (#273): the
                # "waiting on the client" shape, with no colleague riding along on it.
                task = await tasks.create(
                    TaskCreate(
                        title=item.title,
                        description=_task_notes(item, title, locale),
                        company_id=company_id,
                        project_id=project_id,
                        assignee_user_id=(None if item.owner_contact_id else item.assignee_user_id),
                        assignee_contact_id=item.owner_contact_id,
                        due_date=item.due_date or (await _org_today(self.ctx)),
                    )
                )
                task_ids.append(task.id)
            except AppError as exc:
                skipped.append(
                    {"title": item.title, "message": exc.message_key, "fields": exc.fields}
                )
        if task_ids:
            # File the moment onto the tasks it produced, the roster shape (#300).
            from app.modules.interactions.schemas import InteractionUpdate

            await InteractionService(self.ctx).update(
                interaction_id, InteractionUpdate(task_ids=task_ids)
            )
        row = await self.repo.update(
            row,
            title=title,
            minutes=draft.model_dump(mode="json"),
            status=MeetingStatus.DONE.value,
            status_at=_now(),
            interaction_id=interaction_id,
            task_ids=[str(t) for t in task_ids],
            confirmed_at=_now(),
        )
        await ActivityService(self.ctx).record(
            ENTITY_TYPE,
            row.id,
            "meeting.confirmed",
            {"interaction_id": str(interaction_id), "tasks": len(task_ids)},
        )
        return MeetingConfirmResult(
            interaction_id=interaction_id, task_ids=task_ids, skipped=skipped
        )

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


async def org_locale(ctx: Any) -> str:
    """The org's own language — the minutes are the agency's document, not the reviewer's UI."""
    from app.config import settings as app_settings
    from app.core.models import OrgSettings

    locale = await ctx.session.scalar(
        select(OrgSettings.default_locale).where(OrgSettings.org_id == ctx.org.id)
    )
    return locale or app_settings.default_locale


def _task_notes(item: Any, meeting_title: str, locale: str) -> str | None:
    """The task's notes: the item's own, plus the sentence it rests on and where it was said —
    the evidence travels with the work."""
    from app.i18n import translate

    parts: list[str] = []
    if item.description:
        parts.append(item.description.strip())
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
    "INTERACTION_KINDS",
    "MAX_RECORDING_BYTES",
    "MeetingService",
    "MeetingSource",
    "chunk_content_id",
    "drop_audio",
    "group_action_items",
    "participants_of",
    "render_minutes",
    "speaker_names",
]
