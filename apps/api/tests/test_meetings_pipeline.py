"""The meetings pipeline's pure halves: the provider dialects, the cut decision, the labels,
the evidence check. No database, no network."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest

from app.core.ai.providers import ProviderConfig
from app.core.ai.transcribe import (
    Segment,
    _fields,
    parse_transcription,
    speech_base_url,
    speech_limits,
)
from app.modules.meetings import pipeline
from app.modules.meetings.minutes import (
    MAX_TRANSCRIPT_CHARS,
    draft_from_call,
    normalise,
    quote_found,
    transcript_document,
)
from app.modules.meetings.pipeline import (
    NeedsSplit,
    Part,
    fold_chunks,
    parts_needed,
    plan_parts,
    relabel,
)


def _config(provider: str, model: str) -> ProviderConfig:
    return ProviderConfig(provider=provider, api_key="k", model=model)


# --- the three dialects ------------------------------------------------------------------ #
def test_provider_fields_say_what_each_dialect_needs() -> None:
    """Mistral takes ``diarize`` and segment timestamps (and then no ``language``); OpenAI's
    diarize model wants ``diarized_json`` + auto chunking; whisper wants ``verbose_json``; the
    gpt transcribe models answer text and are asked for nothing else."""
    mistral = _fields(
        _config("mistral", "voxtral-mini-latest"), language="nl", diarize=True, timestamps=True
    )
    assert mistral == {
        "model": "voxtral-mini-latest",
        "diarize": "true",
        "timestamp_granularities": "segment",
    }
    diarize = _fields(
        _config("openai", "gpt-4o-transcribe-diarize"), language="nl", diarize=True, timestamps=True
    )
    assert diarize["response_format"] == "diarized_json"
    assert diarize["chunking_strategy"] == "auto"
    assert diarize["language"] == "nl"
    whisper = _fields(_config("openai", "whisper-1"), language="nl", diarize=False, timestamps=True)
    assert whisper["response_format"] == "verbose_json"
    assert whisper["timestamp_granularities[]"] == "segment"
    gpt = _fields(
        _config("openai", "gpt-4o-transcribe"), language="nl", diarize=False, timestamps=False
    )
    assert "response_format" not in gpt


def test_limits_follow_the_vendors_stated_ceilings() -> None:
    assert speech_limits(_config("mistral", "voxtral-mini-latest")).max_seconds > 10_000
    assert speech_limits(_config("mistral", "voxtral-mini-latest")).diarize
    gpt = speech_limits(_config("openai", "gpt-4o-transcribe"))
    assert gpt.max_seconds == 1400 and not gpt.diarize and not gpt.timestamps
    whisper = speech_limits(_config("openai", "whisper-1"))
    assert whisper.max_seconds is None and whisper.timestamps and not whisper.diarize
    assert speech_base_url(_config("mistral", "x")).startswith("https://api.mistral.ai")
    assert speech_base_url(_config("openai", "x")).startswith("https://api.openai.com")


def test_parse_reads_every_dialect_into_one_shape() -> None:
    whisper = parse_transcription(
        {
            "text": "hallo daar",
            "duration": 4.2,
            "segments": [
                {"id": 0, "start": 0.0, "end": 2.1, "text": " hallo "},
                {"start": 2.1, "end": 4.2, "text": "daar"},
            ],
        }
    )
    assert whisper.seconds == 4 and [s.text for s in whisper.segments] == ["hallo", "daar"]
    assert whisper.segments[0].speaker is None

    diarized = parse_transcription(
        {
            "text": "ja nee",
            "segments": [
                {
                    "type": "transcript.text.segment",
                    "speaker": "A",
                    "start": 0,
                    "end": 1,
                    "text": "ja",
                },
                {"speaker": "B", "start": 1, "end": 2.5, "text": "nee"},
            ],
        }
    )
    assert [s.speaker for s in diarized.segments] == ["A", "B"]
    assert diarized.seconds == 2  # from the last segment: the body named no duration

    mistral = parse_transcription(
        {
            "model": "voxtral-mini-latest",
            "text": "goedemorgen",
            "language": "nl",
            "segments": [
                {"text": "goedemorgen", "start": 0.4, "end": 1.9, "speaker_id": "speaker_0"}
            ],
            "usage": {"prompt_audio_seconds": 12, "total_tokens": 40},
        }
    )
    assert mistral.seconds == 12 and mistral.segments[0].speaker == "speaker_0"


# --- the cut decision ------------------------------------------------------------------- #
def test_one_request_when_it_fits_and_parts_when_it_does_not() -> None:
    voxtral = speech_limits(_config("mistral", "voxtral-mini-latest"))
    assert parts_needed(30 * 1024 * 1024, 2 * 3600, voxtral) == 1
    gpt = speech_limits(_config("openai", "gpt-4o-transcribe"))
    # Two hours against a 1400 s cap: six parts by duration, whatever the size.
    assert parts_needed(30 * 1024 * 1024, 2 * 3600, gpt) == 6
    whisper = speech_limits(_config("openai", "whisper-1"))
    # 100 MB against 24 MiB: five parts by bytes, no duration cap.
    assert parts_needed(100 * 1024 * 1024, None, whisper) == 5


def test_a_cut_without_ffmpeg_is_refused_in_one_sentence(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: False)
    gpt = speech_limits(_config("openai", "gpt-4o-transcribe"))
    with pytest.raises(NeedsSplit):
        plan_parts(b"x" * 1000, "webm", 2 * 3600, gpt)
    # …and never for a recording that fits: the sentence is about this recording, not ffmpeg.
    assert plan_parts(b"x" * 1000, "webm", 600, gpt) == [Part(data=b"x" * 1000, offset_seconds=0.0)]


def test_folding_refuses_a_recording_over_the_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "MAX_RECORDING_BYTES", 10)
    assert fold_chunks([b"abc", b"def"]) == b"abcdef"
    with pytest.raises(Exception, match="too_large"):
        fold_chunks([b"abcdef", b"ghijkl"])


def test_speaker_labels_are_numbered_on_across_parts() -> None:
    """A provider labels per request, so a second part's "A" is not the first part's "A": the
    labels continue (S1, S2 · S3, S4) rather than pretending to match, and every timestamp is
    moved by the part's offset."""
    first, nxt = relabel(
        [
            Segment(0, 1, "a", "speaker_0"),
            Segment(1, 2, "b", "speaker_1"),
            Segment(2, 3, "c", "speaker_0"),
        ],
        offset=0.0,
        next_label=1,
    )
    assert [s["speaker"] for s in first] == ["S1", "S2", "S1"]
    second, _ = relabel([Segment(0, 1, "d", "A")], offset=1400.0, next_label=nxt)
    assert second == [{"start": 1400.0, "end": 1401.0, "speaker": "S3", "text": "d"}]
    unlabeled, _ = relabel([Segment(0, 1, "e", None)], offset=0.0, next_label=1)
    assert unlabeled[0]["speaker"] is None


# --- the evidence check --------------------------------------------------------------- #
def test_a_quote_is_found_through_the_recognisers_punctuation() -> None:
    haystack = normalise("Oké, dan zetten we de nieuwe homepage vrijdag 3 oktober live. Prima.")
    assert quote_found("dan zetten we de nieuwe homepage vrijdag 3 oktober live", haystack)
    assert quote_found("Dan zetten we de nieuwe homepage, vrijdag 3 oktober, live!", haystack)
    # Three words match anything in two hours of talk; a short quote does not count.
    assert not quote_found("de nieuwe homepage", haystack)
    assert not quote_found("we lanceren de webshop op maandag", haystack)


def test_the_draft_keeps_an_unquotable_item_and_marks_it() -> None:
    staff = uuid.uuid4()
    transcript = "S1 stelt voor de nieuwsbrief in november te versturen en Femke pakt de teksten op"
    draft = draft_from_call(
        {
            "summary": "Kort overleg.",
            "decisions": [
                {
                    "text": "Nieuwsbrief in november",
                    "quote": "de nieuwsbrief in november te versturen",
                    "at": 12.5,
                },
                {
                    "text": "Verzonnen besluit",
                    "quote": "dit is nooit gezegd in het gesprek",
                    "at": 9999,
                },
            ],
            "action_items": [
                {
                    "title": "Teksten schrijven",
                    "quote": "Femke pakt de teksten op",
                    "assignee_user_id": str(staff),
                    "due_date": "2026-09-25",
                },
                {
                    "title": "Logo aanleveren",
                    "quote": "Femke pakt de teksten op",
                    "assignee_user_id": str(uuid.uuid4()),  # not on the shortlist
                    "owner_label": "Jan (klant)",
                    "due_date": "2031-01-01",  # outside the window
                },
            ],
        },
        transcript_text=transcript,
        staff_ids={str(staff)},
        today=date(2026, 9, 22),
        duration=600,
    )
    assert [d.verified for d in draft.decisions] == [True, False]
    assert draft.decisions[0].at == 12.5
    assert draft.decisions[1].at is None  # past the end of the recording
    first, second = draft.action_items
    assert (
        first.assignee_user_id == staff
        and first.create_task
        and first.due_date == date(2026, 9, 25)
    )
    assert second.assignee_user_id is None and not second.create_task and second.due_date is None
    assert second.owner_label == "Jan (klant)"


def test_the_document_is_data_and_says_when_it_was_cut() -> None:
    long = [{"start": i, "end": i + 1, "speaker": "S1", "text": "x" * 1000} for i in range(400)]
    doc, cut = transcript_document(
        title="Kick-off",
        occurred_at=datetime(2026, 9, 22, 10, 0),
        kind="physical",
        segments=long,
        text=None,
    )
    assert cut and len(doc) < MAX_TRANSCRIPT_CHARS + 20_000
    assert '"transcript_cut_short": true' in doc
    short, cut = transcript_document(
        title="t",
        occurred_at=datetime(2026, 9, 22),
        kind="online",
        segments=[],
        text="alleen tekst",
    )
    assert not cut and '"alleen tekst"' in short


# --- the roster --------------------------------------------------------------------- #
def test_an_owner_is_grounded_in_the_participants_and_a_contact_outranks_a_colleague() -> None:
    """``owner_contact_id`` may only name a contact the PARTICIPANTS block showed the model; an
    item naming both a colleague and a contact is the client's (the "never assign the client's
    promise to a colleague" rule half-obeyed by the model, finished here)."""
    from app.modules.meetings.minutes import participants_block
    from app.modules.meetings.schemas import MeetingParticipant

    staff, contact, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    roster, contact_ids = participants_block(
        [
            MeetingParticipant(name="Sanne", user_id=staff, speaker="S1"),
            MeetingParticipant(name="Jan de Vries", contact_id=contact, speaker="S2"),
            MeetingParticipant(name="Piet (leverancier)"),
        ]
    )
    assert roster.splitlines() == [
        f"S1\tSanne\tstaff\t{staff}",
        f"S2\tJan de Vries\tcontact\t{contact}",
        "-\tPiet (leverancier)\tother\t-",
    ]
    assert contact_ids == {str(contact)}
    transcript = "Jan stuurt het logo en Sanne levert de teksten woensdag aan"
    draft = draft_from_call(
        {
            "summary": "x",
            "action_items": [
                {"title": "Logo", "quote": "Jan stuurt het logo", "owner_contact_id": str(contact)},
                {
                    "title": "Beide genoemd",
                    "quote": "Sanne levert de teksten woensdag aan",
                    "owner_contact_id": str(contact),
                    "assignee_user_id": str(staff),
                },
                {
                    "title": "Onbekende contactpersoon",
                    "quote": "Sanne levert de teksten woensdag aan",
                    "owner_contact_id": str(stranger),
                    "owner_label": "Karel (klant)",
                },
            ],
        },
        transcript_text=transcript,
        staff_ids={str(staff)},
        today=date(2026, 9, 22),
        duration=None,
        contact_ids=contact_ids,
    )
    logo, both, unknown = draft.action_items
    assert logo.owner_contact_id == contact and logo.create_task is False
    assert both.owner_contact_id == contact and both.assignee_user_id is None
    assert unknown.owner_contact_id is None and unknown.owner_label == "Karel (klant)"


def test_the_minutes_print_the_action_items_by_side_and_then_by_person() -> None:
    from app.modules.meetings.schemas import MeetingParticipant, MinutesActionItem, MinutesDraft
    from app.modules.meetings.service import group_action_items, render_minutes

    staff, contact = uuid.uuid4(), uuid.uuid4()
    items = [
        MinutesActionItem(title="Logo sturen", quote="q", owner_contact_id=contact),
        MinutesActionItem(title="Teksten", quote="q", assignee_user_id=staff),
        MinutesActionItem(title="Offerte drukker", quote="q", owner_label="Piet (drukker)"),
        MinutesActionItem(title="Foto's aanleveren", quote="q", owner_contact_id=contact),
        MinutesActionItem(title="Niemand", quote="q"),
    ]
    grouped = group_action_items(items)
    assert [side for side, _ in grouped] == ["agency", "client", "other"]
    client_groups = dict(grouped)["client"]
    assert [key for key, _ in client_groups] == [f"c:{contact}"]
    assert [i.title for i in client_groups[0][1]] == ["Logo sturen", "Foto's aanleveren"]
    other = dict(grouped)["other"]
    assert [key for key, _ in other] == ["n:piet (drukker)", None]

    body = render_minutes(
        MinutesDraft(summary="Kort.", action_items=items),
        locale="nl",
        participants=[
            MeetingParticipant(name="Sanne", user_id=staff, speaker="S1"),
            MeetingParticipant(name="Jan de Vries", contact_id=contact, speaker="S2"),
        ],
        names={f"u:{staff}": "Sanne", f"c:{contact}": "Jan de Vries"},
    )
    assert (
        body.index("## Aanwezig")
        < body.index("Sanne, Jan de Vries")
        < body.index("## Samenvatting")
    )
    ours, theirs, rest = (
        body.index("### Voor ons"),
        body.index("### Voor de klant"),
        body.index("### Overig"),
    )
    assert ours < theirs < rest
    assert ours < body.index("**Sanne**") < body.index("- Teksten") < theirs
    assert theirs < body.index("**Jan de Vries**") < body.index("- Logo sturen") < rest
    assert rest < body.index("**Piet (drukker)**") < body.index("- Niemand")
