"""A meeting is a record of something that happened, so it is never destroyed with the client:
``meetings.company_id`` is ``SET NULL`` and the minutes survive unattached. It hides while the
client is in the trash (the repository's anchor rule) and is detached when the client is purged —
which the dialog says (docs/TRASH.md)."""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

MEETING_TRASH_DEPENDENTS = (
    TrashDependent(
        key="meetings.meetings",
        label_key="trash.dependent.meetings.meetings",
        blocks=False,
        count=count_by_column("meetings", "company_id"),
    ),
)
