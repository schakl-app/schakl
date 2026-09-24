"""A meeting to preview a design against, for an org that has not minuted one yet.

An unsaved :class:`Meeting` instance — never added to a session — carrying the shape a real
row has, so the settings screen's preview runs through the same renderer a download does. The
words are placeholders; the structure (topics, decisions with evidence, action items on both
sides, an open question, a short transcript) is what a design has to lay out.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.modules.meetings.models import Meeting, MeetingKind, MeetingSource, MeetingStatus


def sample_meeting(org_id: uuid.UUID) -> Meeting:
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    staff_id = str(uuid.uuid4())
    contact_id = str(uuid.uuid4())
    return Meeting(
        id=uuid.uuid4(),
        org_id=org_id,
        title="Kick-off nieuwe website",
        kind=MeetingKind.PHYSICAL.value,
        source=MeetingSource.MICROPHONE.value,
        status=MeetingStatus.READY.value,
        status_at=now,
        occurred_at=now,
        language="nl",
        owner_name="Sanne de Vries",
        duration_seconds=47 * 60,
        chunks_received=0,
        participants=[
            {"name": "Sanne de Vries", "user_id": staff_id, "speaker": "S1"},
            {"name": "Jan Jansen", "contact_id": contact_id, "speaker": "S2"},
            {"name": "Piet Bakker (leverancier)", "speaker": "S3"},
        ],
        transcript={
            "segments": [
                {
                    "start": 0,
                    "end": 4,
                    "speaker": "S1",
                    "text": "Goedemorgen allemaal, fijn dat jullie er zijn.",
                },
                {
                    "start": 4,
                    "end": 12,
                    "speaker": "S2",
                    "text": "We willen de nieuwe homepage vrijdag 3 oktober live hebben.",
                },
                {
                    "start": 12,
                    "end": 20,
                    "speaker": "S1",
                    "text": (
                        "Dan lever ik de teksten woensdag aan, en Jan stuurt het logo deze week."
                    ),
                },
                {
                    "start": 20,
                    "end": 27,
                    "speaker": "S3",
                    "text": "De hosting regel ik zodra de DNS is overgezet.",
                },
            ],
            "model": "voxtral-mini-latest",
            "parts": 1,
            "diarized": True,
        },
        transcript_text=(
            "Goedemorgen allemaal, fijn dat jullie er zijn. We willen de nieuwe homepage vrijdag "
            "3 oktober live hebben. Dan lever ik de teksten woensdag aan, en Jan stuurt het logo "
            "deze week. De hosting regel ik zodra de DNS is overgezet."
        ),
        minutes={
            "title": None,
            "summary": (
                "Kick-off van de nieuwe website. De livegang staat op **3 oktober**; de "
                "teksten en het logo worden deze week aangeleverd, de hosting volgt na de "
                "DNS-overzetting."
            ),
            "topics": [
                {
                    "heading": "Planning",
                    "text": (
                        "Livegang op vrijdag 3 oktober, met een week marge voor de laatste "
                        "correcties."
                    ),
                },
                {
                    "heading": "Content",
                    "text": "- Teksten: woensdag\n- Logo: deze week\n- Fotografie: nog te plannen",
                },
            ],
            "decisions": [
                {
                    "text": "De homepage gaat vrijdag 3 oktober live.",
                    "quote": "de nieuwe homepage vrijdag 3 oktober live hebben",
                    "at": 6,
                    "verified": True,
                },
                {
                    "text": "De hosting wordt na de DNS-overzetting geregeld.",
                    "quote": "De hosting regel ik zodra de DNS is overgezet",
                    "at": 21,
                    "verified": True,
                },
            ],
            "action_items": [
                {
                    "title": "Homepageteksten aanleveren",
                    "assignee_user_id": staff_id,
                    "due_date": "2026-09-30",
                    "quote": "Dan lever ik de teksten woensdag aan",
                    "at": 13,
                    "verified": True,
                    "task_id": str(uuid.uuid4()),
                },
                {
                    "title": "Nieuw logo sturen",
                    "owner_contact_id": contact_id,
                    "quote": "Jan stuurt het logo deze week",
                    "at": 16,
                    "verified": True,
                },
                {
                    "title": "Hosting inrichten na DNS",
                    "owner_label": "Piet Bakker (leverancier)",
                    "quote": "De hosting regel ik zodra de DNS is overgezet",
                    "at": 21,
                    "verified": True,
                },
            ],
            "open_questions": ["Wie plant de fotografie in?"],
            "truncated": False,
            "partial_input": False,
        },
    )
