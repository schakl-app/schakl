"""Permissions the meetings module introduces (CLAUDE.md §15).

Three keys. Reading is team-wide (a meeting's minutes are the record of what was agreed with a
client, which is what a colleague picking the work up needs); recording and reviewing are a
member's everyday act; deleting a recording — the audio, the words and the draft together — is
the admin's, exactly as deleting a contact moment is.

What confirming *produces* carries its own gates: the interaction is written through the
interactions module's service (``interactions.interaction.write``) and the tasks through the
tasks module's (``tasks.task.create``), so a person who may review minutes but may not create
tasks is refused at the write, with the key named (#310).
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

MEETING_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("meetings.meeting.read", position=10, default_roles=("admin", "member")),
    PermissionSpec("meetings.meeting.write", position=20, default_roles=("admin", "member")),
    PermissionSpec("meetings.meeting.delete", position=30),
]
