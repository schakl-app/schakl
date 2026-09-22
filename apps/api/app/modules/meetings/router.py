"""REST endpoints for meetings under ``/api/v1/meetings``.

Every route declares a permission (§15). The whole router sits behind the module's licence
write gate: past expiry an agency keeps reading its minutes and stops recording new ones.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.entitlements.service import license_write_gate
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.meetings.schemas import (
    MeetingChunk,
    MeetingConfirm,
    MeetingConfirmResult,
    MeetingCreate,
    MeetingDetail,
    MeetingFinish,
    MeetingList,
    MeetingSpeakers,
    MeetingStatusRead,
    MeetingUpdate,
    MinutesDraft,
)
from app.modules.meetings.service import MeetingService

router = APIRouter(
    prefix="/meetings", tags=["meetings"], dependencies=[license_write_gate("meetings")]
)


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
    "/{meeting_id}/speakers",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def set_speakers(
    meeting_id: uuid.UUID, payload: MeetingSpeakers, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    """Name the provider's speaker labels — "S2 is Jan"."""
    return await MeetingService(ctx).set_speakers(meeting_id, payload.speakers)


@router.put(
    "/{meeting_id}/minutes",
    response_model=MeetingDetail,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def save_minutes(
    meeting_id: uuid.UUID, payload: MinutesDraft, ctx: RequestContext = Depends(require_context)
) -> MeetingDetail:
    """The reviewer's edits to the draft, kept without confirming."""
    return await MeetingService(ctx).save_minutes(meeting_id, payload)


@router.post(
    "/{meeting_id}/confirm",
    response_model=MeetingConfirmResult,
    dependencies=[require_permission("meetings.meeting.write")],
)
async def confirm_meeting(
    meeting_id: uuid.UUID, payload: MeetingConfirm, ctx: RequestContext = Depends(require_context)
) -> MeetingConfirmResult:
    """The minutes become a contact moment and the ticked action items become tasks."""
    return await MeetingService(ctx).confirm(meeting_id, payload)


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
