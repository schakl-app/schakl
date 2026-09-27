"""Bulk actions on meetings (CLAUDE.md §18).

Delete only, and for the reason invoices and contact moments are: a meeting has no import
shape, and nothing about one is a value a selection could share — a title, a client and a day
are each that meeting's own. What a register of recordings does collect is rows nobody wants:
a test recording, a meeting started twice, a run that failed on a dead microphone.

Every row goes through :meth:`MeetingService.delete`, so a meeting a worker is still reading
is refused by name (``meetings.error.busy``), the audio is dropped through the storage
service rather than left behind, and the trail line is written — exactly what fifty visits to
the page's own delete would have done.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor
from app.core.tenancy import RequestContext
from app.modules.meetings.models import Meeting
from app.modules.meetings.service import MeetingService


async def _delete(ctx: RequestContext, meeting: Any) -> None:
    await MeetingService(ctx).delete(meeting.id)


MEETING_BULK = BulkDescriptor(
    model=Meeting,
    entity="meeting",
    delete_permission="meetings.meeting.delete",
    delete_row=_delete,
)
