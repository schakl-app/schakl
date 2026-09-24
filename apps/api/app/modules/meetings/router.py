"""REST endpoints for meetings under ``/api/v1/meetings``.

Every route declares a permission (§15). The whole router sits behind the module's licence
write gate: past expiry an agency keeps reading its minutes and stops recording new ones.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response

from app.core.ai.schemas import TaskParseResult, TimeTranscribeResult
from app.core.entitlements.service import license_write_gate
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.modules.meetings.models import DOCUMENT_SECTIONS
from app.modules.meetings.schemas import (
    MeetingChunk,
    MeetingCreate,
    MeetingDesignSource,
    MeetingDetail,
    MeetingFinish,
    MeetingList,
    MeetingLogTime,
    MeetingParticipants,
    MeetingPolicy,
    MeetingReviseRequest,
    MeetingReviseResult,
    MeetingSectionCatalogEntry,
    MeetingSettingsPreviewRequest,
    MeetingSettingsRead,
    MeetingSettingsUpdate,
    MeetingStatusRead,
    MeetingTaskCreate,
    MeetingTaskCreated,
    MeetingTaskDraftRequest,
    MeetingTranscribeRequest,
    MeetingUpdate,
    MinutesDraft,
)
from app.modules.meetings.service import MeetingService
from app.modules.meetings.settings import MeetingSettingsService
from app.modules.meetings.transcript import MEDIA_TYPES as TRANSCRIPT_MEDIA_TYPES
from app.modules.meetings.transcript import render_transcript, transcript_filename

router = APIRouter(
    prefix="/meetings", tags=["meetings"], dependencies=[license_write_gate("meetings")]
)


# --- org settings, the recorder's policy, the document (literal paths first) -------------- #
@router.get(
    "/settings",
    response_model=MeetingSettingsRead,
    dependencies=[require_permission("meetings.settings.manage")],
)
async def get_settings(ctx: RequestContext = Depends(require_context)) -> MeetingSettingsRead:
    """The org's meetings settings: the consent statement, the minutes document, the house
    writing instructions. No saved row means the defaults."""
    return await MeetingSettingsService(ctx).settings()


@router.put(
    "/settings",
    response_model=MeetingSettingsRead,
    dependencies=[require_permission("meetings.settings.manage")],
)
async def update_settings(
    payload: MeetingSettingsUpdate, ctx: RequestContext = Depends(require_context)
) -> MeetingSettingsRead:
    return await MeetingSettingsService(ctx).update(payload)


@router.post(
    "/settings/preview",
    dependencies=[require_permission("meetings.settings.manage")],
)
async def preview_settings(
    payload: MeetingSettingsPreviewRequest, ctx: RequestContext = Depends(require_context)
) -> Response:
    """Render an unsaved design over the org's latest minuted meeting — the editor's preview."""
    html = await MeetingSettingsService(ctx).preview(payload)
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get(
    "/settings/designs/{design}/source",
    response_model=MeetingDesignSource,
    dependencies=[require_permission("meetings.settings.manage")],
)
async def design_source(
    design: str, ctx: RequestContext = Depends(require_context)
) -> MeetingDesignSource:
    """A shipped design's own HTML and CSS, to start a custom minutes template from."""
    return MeetingSettingsService(ctx).source(design)


@router.get(
    "/settings/sections",
    response_model=list[MeetingSectionCatalogEntry],
    dependencies=[require_permission("meetings.settings.manage")],
)
async def section_catalog(
    ctx: RequestContext = Depends(require_context),
) -> list[MeetingSectionCatalogEntry]:
    """Every chapter a minutes document can carry, with the org's default for each."""
    return await MeetingSettingsService(ctx).catalog()


@router.get(
    "/policy",
    response_model=MeetingPolicy,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def meeting_policy(ctx: RequestContext = Depends(require_context)) -> MeetingPolicy:
    """What the recorder asks before it records: readable by whoever may record."""
    return await MeetingSettingsService(ctx).policy()




@router.get(
    "", response_model=MeetingList, dependencies=[require_permission("meetings.meeting.read")]
)
async def list_meetings(
    company_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None, description="Comma-separated set; absent means every status"),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    count: bool = Query(True),
    ctx: RequestContext = Depends(require_context),
) -> MeetingList:
    return await MeetingService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=company_id,
        project_id=project_id,
        status=status,
        q=q,
        count=count,
    )


@router.post(
    "",
    response_model=MeetingDetail,
    status_code=201,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def create_meeting(
    payload: MeetingCreate, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    """Open a recording. Refused unless the caller states the participants were told."""
    return await MeetingService(ctx).create(payload)


@router.get(
    "/{meeting_id}",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.read")],
)
async def get_meeting(
    meeting_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    return await MeetingService(ctx).get(meeting_id)


@router.get(
    "/{meeting_id}/status",
    response_model=MeetingStatusRead,
    dependencies=[require_permission("meetings.meeting.read")],
)
async def meeting_status(
    meeting_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MeetingStatusRead:
    """The one column the detail page polls while a worker holds the row."""
    return await MeetingService(ctx).status(meeting_id)


@router.patch(
    "/{meeting_id}",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def update_meeting(
    meeting_id: uuid.UUID, payload: MeetingUpdate, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    return await MeetingService(ctx).update(meeting_id, payload)


@router.post(
    "/{meeting_id}/chunks",
    dependencies=[require_permission("meetings.meeting.write")],
)
async def add_chunk(
    meeting_id: uuid.UUID, payload: MeetingChunk, ctx: RequestContext = Depends(require_context)
) -> dict[str, int]:
    """One piece of the recording, base64 in JSON, while it is still being recorded."""
    return {"chunks_received": await MeetingService(ctx).add_chunk(meeting_id, payload)}


@router.post(
    "/{meeting_id}/finish",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def finish_meeting(
    meeting_id: uuid.UUID, payload: MeetingFinish, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    """The recorder stopped: the worker folds, transcribes and drafts."""
    return await MeetingService(ctx).finish(meeting_id, payload)


@router.post(
    "/{meeting_id}/retry",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def retry_meeting(
    meeting_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    return await MeetingService(ctx).retry(meeting_id)


@router.put(
    "/{meeting_id}/participants",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def set_participants(
    meeting_id: uuid.UUID,
    payload: MeetingParticipants,
    ctx: RequestContext = Depends(require_context),
) -> MeetingDetail:
    """Who was in the meeting — a colleague, a contact of the client, or a name — and which
    speaker label (S1, S2 …) each of them is. Replaces the whole roster."""
    return await MeetingService(ctx).set_participants(meeting_id, payload.participants)


@router.post(
    "/{meeting_id}/redraft",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def redraft_meeting(
    meeting_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    """Draft the minutes again over the transcript already here — after naming the speakers,
    so the action items land on the people who took them on. No new transcription."""
    return await MeetingService(ctx).redraft(meeting_id)


@router.put(
    "/{meeting_id}/minutes",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def save_minutes(
    meeting_id: uuid.UUID, payload: MinutesDraft, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    """An edit to the minutes, whole. The page autosaves through this; the contact moment on
    the client is rewritten to match. A title in the draft is the meeting's title."""
    return await MeetingService(ctx).save_minutes(meeting_id, payload)


@router.post(
    "/{meeting_id}/interaction",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def file_meeting(
    meeting_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    """File the minutes as a contact moment on the client now. Normally that happened by
    itself the moment the minutes landed; this is for a meeting where it did not (the filing
    was refused, or the row predates it), and it says why when it is refused again."""
    return await MeetingService(ctx).file_interaction(meeting_id)


@router.post(
    "/{meeting_id}/time",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def log_meeting_time(
    meeting_id: uuid.UUID, payload: MeetingLogTime, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    """Book the meeting's hours: one time entry per colleague named, for its length (or the
    minutes given), filed on the contact moment. Booking a colleague asks
    ``time.entry.write:any``; the ``time`` module must be writable."""
    return await MeetingService(ctx).log_time(meeting_id, payload)


@router.post(
    "/{meeting_id}/action-items/draft-task",
    response_model=TaskParseResult,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def draft_task_for_action_item(
    meeting_id: uuid.UUID,
    payload: MeetingTaskDraftRequest,
    ctx: RequestContext = Depends(require_context),
) -> TaskParseResult:
    """Schakl fills in the task form for one action item of the minutes — title, what exactly,
    the steps the meeting listed, the deadline that was spoken, who took it on — grounded in
    the transcript around it. A draft: nothing is created until it is posted below."""
    return await MeetingService(ctx).draft_task(meeting_id, payload)


@router.post(
    "/{meeting_id}/action-items/task",
    response_model=MeetingTaskCreated,
    status_code=201,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def create_task_for_action_item(
    meeting_id: uuid.UUID,
    payload: MeetingTaskCreate,
    ctx: RequestContext = Depends(require_context),
) -> MeetingTaskCreated:
    """Make the task for one action item now, with its steps and links in one call, as the
    caller (the tasks module's own rules apply). The item remembers its task and the contact
    moment lists it; the only way an action item becomes a task."""
    return await MeetingService(ctx).create_task_for_item(meeting_id, payload)


@router.delete(
    "/{meeting_id}/audio",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def delete_audio(
    meeting_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    """Drop the recording now rather than at the retention date; the words stay."""
    return await MeetingService(ctx).delete_audio(meeting_id)


@router.delete(
    "/{meeting_id}",
    status_code=204,
    dependencies=[require_permission("meetings.meeting.delete")],
)
async def delete_meeting(
    meeting_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await MeetingService(ctx).delete(meeting_id)


@router.get(
    "/{meeting_id}/transcript",
    dependencies=[require_permission("meetings.meeting.read")],
)
async def meeting_transcript(
    meeting_id: uuid.UUID,
    format: str = Query("json", pattern="^(json|txt|md|srt|vtt)$"),
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """The transcript, whole: as JSON (every line with its speaker's name and the seconds it
    was said at — what an agent reads), or as a `.txt`, `.md`, `.srt` or `.vtt` file."""
    transcript = await MeetingService(ctx).transcript(meeting_id)
    if format == "json":
        return Response(content=transcript.model_dump_json(), media_type="application/json")
    body = render_transcript(transcript, format)
    filename = transcript_filename(transcript.title, format)
    return Response(
        content=body,
        media_type=TRANSCRIPT_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _sections_param(sections: str | None) -> list[str] | None:
    if sections is None:
        return None
    wanted = [s.strip() for s in sections.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in DOCUMENT_SECTIONS]
    if unknown:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"sections": "meetings.error.unknown_section"},
            details={"unknown": unknown, "known": list(DOCUMENT_SECTIONS)},
        )
    return wanted


@router.get(
    "/{meeting_id}/preview",
    dependencies=[require_permission("meetings.meeting.read")],
)
async def preview_meeting(
    meeting_id: uuid.UUID,
    sections: str | None = Query(
        None, description="Comma-separated chapters; absent = the org's defaults"
    ),
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """The minutes document as HTML — the same artefact the PDF prints."""
    from app.modules.meetings.render import render_meeting_html

    service = MeetingService(ctx)
    ctx.require("meetings.meeting.read")
    row = await service.repo.get_or_404(meeting_id)
    html = await render_meeting_html(ctx, row, sections=_sections_param(sections))
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get(
    "/{meeting_id}/pdf",
    dependencies=[require_permission("meetings.meeting.read")],
)
async def meeting_pdf(
    meeting_id: uuid.UUID,
    sections: str | None = Query(
        None, description="Comma-separated chapters; absent = the org's defaults"
    ),
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """The minutes as a PDF, with the chapters the caller ticked (the transcript is off unless
    asked for). A read, so it survives an expired licence."""
    from app.modules.meetings.render import render_meeting_pdf

    service = MeetingService(ctx)
    ctx.require("meetings.meeting.read")
    row = await service.repo.get_or_404(meeting_id)
    content, filename = await render_meeting_pdf(ctx, row, sections=_sections_param(sections))
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- changed in words -------------------------------------------------------------------- #
@router.post(
    "/{meeting_id}/ai/revise",
    response_model=MeetingReviseResult,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def revise_meeting_with_ai(
    meeting_id: uuid.UUID,
    payload: MeetingReviseRequest,
    ctx: RequestContext = Depends(require_context),
) -> MeetingReviseResult:
    """One typed instruction, applied to the meeting as the caller: the title, client, project,
    kind and date, the roster and its speaker labels, and — while under review — every part of
    the minutes. The answer is a diff; what the instruction did not mention stays."""
    from app.modules.meetings.assist import revise_meeting

    return await revise_meeting(ctx, meeting_id, payload)


@router.post(
    "/{meeting_id}/ai/transcribe",
    response_model=TimeTranscribeResult,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def transcribe_revise_instruction(
    meeting_id: uuid.UUID,
    payload: MeetingTranscribeRequest,
    ctx: RequestContext = Depends(require_context),
) -> TimeTranscribeResult:
    """Speech to text for the revise box: an instruction spoken instead of typed. Nothing is
    written; the words come back to be read first."""
    from app.modules.meetings.assist import transcribe_instruction

    return await transcribe_instruction(ctx, meeting_id, payload)
