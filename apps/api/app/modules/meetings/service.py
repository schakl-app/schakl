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
from app.core.directory import labels_for
from app.core.jobs import enqueue
from app.core.parent import ensure_parent_in_tenant
from app.core.storage.models import StoredFile
from app.core.storage.service import drop_file
from app.core.storage.system import store_system_file
from app.core.tenancy import RequestContext
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
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Meeting)

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
            speakers={str(k): str(v) for k, v in (row.speakers or {}).items()},
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
        )
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, row.id)
        return await self._detail(row)

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

    async def _enqueue(self, row: Meeting) -> None:
        # A fresh id per queue: arq declines a job whose *result* is still in Redis, and a
        # retry an hour after the first run would otherwise queue nothing and sit on
        # ``queued`` until the reaper called it failed (``core.jobs.enqueue``'s own warning).
        job = await enqueue(
            "meetings_process",
            str(self.ctx.org.id),
            str(row.id),
            _job_id=f"meetings-process-{row.id}-{int(_now().timestamp())}",
        )
        if job is None:
            await self.repo.update(
                row, status=MeetingStatus.FAILED.value, error_key="meetings.error.not_queued"
            )

    async def set_speakers(self, meeting_id: uuid.UUID, speakers: dict[str, str]) -> MeetingDetail:
        row = await self._writable(meeting_id)
        cleaned = {
            str(k)[:20]: str(v).strip()[:255] for k, v in speakers.items() if str(v).strip()
        }
        row = await self.repo.update(row, speakers=cleaned)
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
        body = render_minutes(draft, locale=locale, speakers=row.speakers or {})
        kind = data.interaction_kind or INTERACTION_KINDS.get(row.kind, "physical_meeting")
        interaction = await InteractionService(self.ctx).create(
            InteractionCreate(
                kind=kind,
                occurred_at=row.occurred_at,
                subject=title[:500],
                body_text=body,
                company_id=row.company_id,
                project_id=row.project_id,
            )
        )
        interaction_id = uuid.UUID(str(interaction["id"]))

        tasks = TaskService(self.ctx)
        task_ids: list[uuid.UUID] = []
        skipped: list[dict[str, Any]] = []
        for item in draft.action_items:
            if not item.create_task:
                continue
            try:
                async with self.ctx.session.begin_nested():
                    task = await tasks.create(
                        TaskCreate(
                            title=item.title,
                            description=_task_notes(item, title, locale),
                            company_id=row.company_id,
                            project_id=row.project_id,
                            assignee_user_id=item.assignee_user_id,
                            due_date=item.due_date
                            or (await _org_today(self.ctx)),
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


def render_minutes(draft: MinutesDraft, *, locale: str, speakers: dict[str, str]) -> str:
    """The minutes as the markdown the contact moment carries — headings in the org's language,
    the reviewer's words verbatim, every claim with its timestamp beside it."""
    from app.i18n import translate

    lines: list[str] = []
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
        for item in draft.action_items:
            owner = item.owner_label.strip() if item.owner_label else ""
            due = f" — {item.due_date.isoformat()}" if item.due_date else ""
            who = f" ({owner})" if owner else ""
            at = f" _({_clock(item.at)})_" if item.at is not None else ""
            lines.append(f"- {item.title.strip()}{who}{due}{at}")
        lines.append("")
    if draft.open_questions:
        lines += [f"## {translate('meetings.minutes.heading_questions', locale)}", ""]
        lines += [f"- {q.strip()}" for q in draft.open_questions]
        lines.append("")
    return "\n".join(lines).strip()


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
    "render_minutes",
]
