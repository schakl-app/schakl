"""The meetings pipeline's pure halves: the provider dialects, the cut decision, the labels,
the evidence check. No database, no network."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from app.core.ai import transcribe as transcribe_module
from app.core.ai.audio import AudioClip
from app.core.ai.providers import AIProviderError, ProviderConfig
from app.core.ai.transcribe import (
    KnownSpeaker,
    Segment,
    Transcript,
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
    OVERLAP_SECONDS,
    NeedsSplit,
    Part,
    TranscribedRecording,
    align_labels,
    fold_chunks,
    parts_needed,
    plan_parts,
    plan_windows,
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
    assert gpt.max_seconds == 1500 and not gpt.diarize and not gpt.timestamps
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
    # Two hours against the 1500 s cap (with the pipeline's own margin): five parts by
    # duration, whatever the size.
    assert parts_needed(30 * 1024 * 1024, 2 * 3600, gpt) == 5
    # A twenty-three-minute meeting is **one** request: it used to be two (a margin taken
    # twice, 1400 × 0.97), which is what handed a two-person meeting four speaker labels.
    assert parts_needed(6 * 1024 * 1024, 23 * 60, gpt) == 1
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


def test_a_cut_recording_is_cut_with_an_overlap_where_the_provider_timestamps(monkeypatch) -> None:
    """Forty minutes against the 1500 s cap is two parts, and the second starts
    ``OVERLAP_SECONDS`` before the first ends — that overlap is what the speaker labels are
    matched on afterwards. A text-only model gets edge-to-edge parts: nothing to align, and an
    overlap would be the same minute transcribed twice."""
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: True)
    seen: list[list[tuple[float, float]]] = []
    monkeypatch.setattr(
        pipeline, "cut_with_ffmpeg", lambda data, ext, windows: seen.append(windows) or []
    )
    diarize = speech_limits(_config("openai", "gpt-4o-transcribe-diarize"))
    plan_parts(b"x" * 1000, "webm", 40 * 60, diarize)
    (first, second) = seen[-1]
    assert first[0] == 0.0
    assert second[0] == pytest.approx(first[1] - OVERLAP_SECONDS)
    assert second[0] + second[1] == pytest.approx(40 * 60)
    text_only = speech_limits(_config("openai", "gpt-4o-transcribe"))
    plan_parts(b"x" * 1000, "webm", 40 * 60, text_only)
    (first, second) = seen[-1]
    assert second[0] == pytest.approx(first[1])


def test_windows_cover_the_recording_and_leave_no_sliver() -> None:
    """1386 s in two parts with a 45 s overlap is two parts of 717 s (the overlap is paid for
    by the part length, ``plan_parts``): the second starts at 672 and ends at 1386, and a
    third window of a fraction of a second is never planned."""
    assert plan_windows(1386.0, 717, 45) == [(0.0, 717.0), (672.0, 714.0)]
    # Edge to edge, no overlap: the old shape, the old sliver rule.
    assert plan_windows(1386.0, 694, 0) == [(0.0, 694.0), (694.0, 692.0)]
    assert plan_windows(600.0, 694, 45) == [(0.0, 600.0)]


def test_a_sub_second_tail_from_the_cut_is_never_sent(monkeypatch) -> None:
    durations = {"part0000.webm": 693.008, "part0001.webm": 0.027}

    def fake_run(args, **_kwargs):  # noqa: ANN001, ANN202
        Path(args[-1]).write_bytes(Path(args[-1]).name.encode())
        return pipeline.subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "_probe_duration", lambda path: durations[path.name])
    parts = pipeline.cut_with_ffmpeg(b"audio", "webm", [(0.0, 693.0), (693.0, 0.5)])
    assert [p.data for p in parts] == [b"part0000.webm"]
    assert parts[0].length_seconds == pytest.approx(693.008)


def test_a_remux_is_skipped_without_ffmpeg_and_for_a_finished_container(monkeypatch) -> None:
    """A phone's m4a states its length already; a browser's WebM does not, but on a box with
    no ffmpeg the bytes are kept as they came rather than refused."""
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: False)
    assert pipeline.remux(b"webm", "webm") is None
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: True)
    assert pipeline.remux(b"m4a", "m4a") is None


async def test_a_provider_that_does_not_answer_is_a_provider_error(monkeypatch) -> None:
    """A read timeout used to escape as a bare httpx exception, which the meeting run filed as
    a crash of ours (``meetings.error.failed``) — every recording over a quarter of an hour."""

    class _Silent:
        async def post(self, *_args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            # A transcription is given more than the chat client's 180 s.
            assert kwargs["timeout"].read >= 900
            raise httpx.ReadTimeout("no answer")

    monkeypatch.setattr(transcribe_module, "client", lambda: _Silent())
    clip = AudioClip(data=b"x", content_type="audio/webm", extension="webm")
    with pytest.raises(AIProviderError, match="ReadTimeout"):
        await transcribe_module.transcribe(
            _config("openai", "gpt-4o-transcribe-diarize"), clip, language="nl"
        )


def test_a_meeting_run_outlives_arqs_five_minute_default() -> None:
    from app.modules.meetings import jobs
    from app.registry import registry

    (fn,) = registry.get("meetings").worker_functions
    assert fn.name == "meetings_process"
    assert 30 * 60 <= fn.timeout_s < jobs.STALE_AFTER_MINUTES * 60


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


def test_a_speaker_keeps_their_label_across_the_cut() -> None:
    """The first part ends at 100 s; the second was cut from 55 s. In the 45 s both parts
    transcribed, the second part's ``A`` spoke while S1 spoke and its ``B`` while S2 spoke —
    so they *are* S1 and S2, a third voice heard only after the cut is S3, and the rows the
    first part already carries are not repeated."""
    previous = [
        {"start": 0.0, "end": 50.0, "speaker": "S1", "text": "intro"},
        {"start": 55.0, "end": 70.0, "speaker": "S1", "text": "een"},
        {"start": 70.0, "end": 85.0, "speaker": "S2", "text": "twee"},
        {"start": 85.0, "end": 100.0, "speaker": "S1", "text": "drie"},
    ]
    incoming = [
        Segment(0.0, 15.0, "een", "A"),  # 55–70 absolute: S1's seconds
        Segment(15.0, 30.0, "twee", "B"),  # 70–85: S2's
        Segment(30.0, 44.0, "drie", "A"),  # 85–99: S1's
        Segment(44.0, 60.0, "vier", "B"),  # straddles 100: mostly past it → kept, as S2
        Segment(60.0, 80.0, "vijf", "C"),  # after the cut only: a new voice
    ]
    rows, nxt = align_labels(previous, incoming, offset=55.0, window_end=100.0, next_label=3)
    assert [(r["speaker"], r["text"]) for r in rows] == [("S2", "vier"), ("S3", "vijf")]
    assert rows[0]["start"] == 99.0 and nxt == 4


def test_a_label_the_overlap_cannot_pair_gets_a_fresh_number() -> None:
    """Two seconds of cross-talk with S1 is not evidence that ``A`` is S1: the failure
    direction is the old one (a speaker split in two), never two people merged."""
    previous = [{"start": 55.0, "end": 100.0, "speaker": "S1", "text": "monoloog"}]
    incoming = [Segment(0.0, 1.5, "hm", "A"), Segment(50.0, 60.0, "later", "A")]
    rows, nxt = align_labels(previous, incoming, offset=55.0, window_end=100.0, next_label=2)
    assert [r["speaker"] for r in rows] == ["S2"] and nxt == 3


async def test_overlapping_parts_are_read_as_one_transcript(monkeypatch) -> None:
    """The whole thing over a fake provider: two parts, the labels aligned, the flat text
    rebuilt from the rows so the overlap is never read twice, and the row says it aligned."""
    parts = [
        Part(data=b"one", offset_seconds=0.0, length_seconds=100.0),
        Part(data=b"two", offset_seconds=55.0, length_seconds=60.0),
    ]
    monkeypatch.setattr(pipeline, "plan_parts", lambda *a, **k: parts)
    answers = {
        b"one": Transcript(
            "hallo. ja.",
            100,
            (Segment(0, 50, "hallo", "spk0"), Segment(60, 95, "ja", "spk1")),
        ),
        b"two": Transcript(
            "ja. dag.",
            60,
            (Segment(5, 40, "ja", "x"), Segment(48, 60, "dag", "x")),
        ),
    }

    async def fake(config, clip, **_kw):  # noqa: ANN001, ANN003
        return answers[clip.data]

    monkeypatch.setattr(pipeline, "provider_transcribe", fake)
    result: TranscribedRecording = await pipeline.transcribe_recording(
        _config("openai", "gpt-4o-transcribe-diarize"),
        b"audio",
        "webm",
        language="nl",
        duration_seconds=115,
    )
    assert result.parts == 2 and result.aligned
    assert [(r["speaker"], r["text"]) for r in result.segments] == [
        ("S1", "hallo"),
        ("S2", "ja"),
        ("S2", "dag"),
    ]
    assert result.text == "hallo ja dag"
    assert result.seconds == 115


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
    assert first.assignee_user_id == staff and first.due_date == date(2026, 9, 25)
    assert second.assignee_user_id is None and second.due_date is None
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
    assert logo.owner_contact_id == contact
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


# --- the voices across the cuts ---------------------------------------------------------- #
def test_the_diarize_dialect_carries_the_voices_it_already_heard() -> None:
    """OpenAI's diarize model takes ``known_speaker_names[]`` / ``known_speaker_references[]``
    as repeated multipart fields, the sample as a data URL; no other dialect is told about
    them, whatever the caller offers."""
    sample = AudioClip(data=b"\x1a\x45\xdf\xa3opus", content_type="audio/webm", extension="webm")
    known = [KnownSpeaker("S1", sample), KnownSpeaker("S2", sample)]
    fields = _fields(
        _config("openai", "gpt-4o-transcribe-diarize"),
        language="nl",
        diarize=True,
        timestamps=True,
        known=known,
    )
    assert fields["known_speaker_names[]"] == ["S1", "S2"]
    refs = fields["known_speaker_references[]"]
    assert isinstance(refs, list) and len(refs) == 2
    assert refs[0].startswith("data:audio/webm;base64,")
    for provider, model in (("mistral", "voxtral-mini-latest"), ("openai", "whisper-1")):
        fields = _fields(
            _config(provider, model), language="nl", diarize=True, timestamps=True, known=known
        )
        assert not any(k.startswith("known_speaker") for k in fields)
    assert speech_limits(_config("openai", "gpt-4o-transcribe-diarize")).known_speakers == 4
    assert speech_limits(_config("mistral", "voxtral-mini-latest")).known_speakers == 0


def test_a_voice_the_provider_kept_takes_its_label_before_any_pairing() -> None:
    """The new part came back with ``S2`` for the voice it was handed a sample of, even though
    by the clock that voice overlaps S1's rows (two independent diarizations disagree on a
    boundary): the provider's recognition of a voice outranks an inference from timing, S1
    stays free for the label the provider did *not* name, and nothing is numbered afresh."""
    previous = [
        {"start": 0.0, "end": 60.0, "speaker": "S1", "text": "intro"},
        {"start": 60.0, "end": 85.0, "speaker": "S2", "text": "antwoord"},
        {"start": 85.0, "end": 100.0, "speaker": "S1", "text": "drie"},
    ]
    incoming = [
        Segment(0.0, 10.0, "intro", "S2"),  # 55–65 absolute: S1's seconds by the clock
        Segment(20.0, 30.0, "antwoord", "A"),  # 75–85: S2's seconds — but S2 is spoken for
        Segment(30.0, 44.0, "drie", "A"),  # 85–99: S1's seconds, and most of A's
        Segment(50.0, 70.0, "verder", "S2"),  # after the cut
        Segment(70.0, 80.0, "nog iets", "A"),
    ]
    rows, nxt = align_labels(
        previous, incoming, offset=55.0, window_end=100.0, next_label=3, known=frozenset({"S2"})
    )
    assert [(r["speaker"], r["text"]) for r in rows] == [("S2", "verder"), ("S1", "nog iets")]
    assert nxt == 3


def test_reference_windows_pick_the_people_who_spoke_most_from_their_longest_stretch() -> None:
    """Four names is the provider's bound, so the four who carried the meeting are sampled;
    each sample is that person's longest single stretch, cut a little inside its own ends and
    capped at the target; a person with no stretch long enough is left for the overlap."""
    segments = [
        {"start": 0.0, "end": 30.0, "speaker": "S1", "text": "a"},
        {"start": 30.0, "end": 31.0, "speaker": "S2", "text": "hm"},
        {"start": 31.0, "end": 36.0, "speaker": "S3", "text": "c"},
        {"start": 36.0, "end": 37.0, "speaker": "S2", "text": "ja"},
        {"start": 37.0, "end": 41.0, "speaker": "S4", "text": "d"},
        {"start": 41.0, "end": 42.0, "speaker": "S5", "text": "e"},
    ]
    windows = pipeline.reference_windows(segments, limit=4, min_seconds=2.0, max_seconds=10.0)
    assert [w[0] for w in windows] == ["S1", "S3", "S4"]  # S2's two short turns cannot be sampled
    s1, s3, s4 = windows
    assert s1[1] == pytest.approx(0.25) and s1[2] == pytest.approx(8.0)  # capped at the target
    assert s3[1] == pytest.approx(31.25) and s3[2] == pytest.approx(4.5)
    assert s4[1] == pytest.approx(37.25) and s4[2] == pytest.approx(3.5)
    # The bound holds: three names when three are asked for, most-spoken first.
    assert [
        w[0]
        for w in pipeline.reference_windows(segments, limit=2, min_seconds=2.0, max_seconds=10.0)
    ] == ["S1", "S3"]


def test_reference_clips_are_cut_from_the_recording_and_measured(monkeypatch) -> None:
    """A clip the cut measured outside the vendor's 2–10 s is left out rather than sent (a
    refused request costs the whole part), and without ffmpeg there are no clips at all."""
    segments = [
        {"start": 0.0, "end": 30.0, "speaker": "S1", "text": "a"},
        {"start": 30.0, "end": 36.0, "speaker": "S2", "text": "b"},
    ]
    limits = speech_limits(_config("openai", "gpt-4o-transcribe-diarize"))
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: False)
    assert pipeline.reference_clips(b"audio", "webm", segments, limits=limits) == []
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: True)
    seen: list[list[tuple[float, float]]] = []

    def fake_cut(data, ext, windows):  # noqa: ANN001, ANN202
        seen.append(windows)
        return [
            Part(data=b"s1", offset_seconds=windows[0][0], length_seconds=8.0),
            Part(data=b"s2", offset_seconds=windows[1][0], length_seconds=1.2),  # too short
        ]

    monkeypatch.setattr(pipeline, "cut_windows", fake_cut)
    clips = pipeline.reference_clips(b"audio", "webm", segments, limits=limits)
    assert [(c.name, c.sample.data, c.sample.content_type) for c in clips] == [
        ("S1", b"s1", "audio/webm")
    ]
    assert seen == [[(0.25, 8.0), (30.25, 5.5)]]


def test_labels_are_renumbered_densely_after_the_parts() -> None:
    """Numbering on through the parts leaves holes where a label's every row fell inside an
    overlap: thirteen voices counted up to S17. Renumbered by first appearance, in place."""
    rows = [
        {"start": 0, "end": 1, "speaker": "S1", "text": "a"},
        {"start": 1, "end": 2, "speaker": "S3", "text": "b"},
        {"start": 2, "end": 3, "speaker": None, "text": "c"},
        {"start": 3, "end": 4, "speaker": "S7", "text": "d"},
        {"start": 4, "end": 5, "speaker": "S3", "text": "e"},
    ]
    assert pipeline.compact_labels(rows) == {"S1": "S1", "S3": "S2", "S7": "S3"}
    assert [r["speaker"] for r in rows] == ["S1", "S2", None, "S3", "S2"]


async def test_every_later_part_is_handed_the_voices_of_the_parts_before_it(monkeypatch) -> None:
    """Three parts over a fake provider that, like OpenAI's, answers a known name for a voice
    it was given and a letter for one it was not: the second part is asked with the first
    part's two voices, the third with all three, and the transcript ends with three labels —
    not seven — with the row saying the voices were matched."""
    parts = [
        Part(data=b"one", offset_seconds=0.0, length_seconds=100.0),
        Part(data=b"two", offset_seconds=55.0, length_seconds=100.0),
        Part(data=b"three", offset_seconds=110.0, length_seconds=60.0),
    ]
    monkeypatch.setattr(pipeline, "plan_parts", lambda *a, **k: parts)
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        pipeline,
        "cut_windows",
        lambda data, ext, windows: [
            Part(data=f"clip{i}".encode(), offset_seconds=s, length_seconds=length)
            for i, (s, length) in enumerate(windows)
        ],
    )
    asked: list[list[str]] = []
    answers = {
        b"one": (Segment(0, 50, "hallo", "A"), Segment(50, 95, "ja", "B")),
        # 55–155: S1 and S2 recognised by name; a third voice, "A", is new.
        b"two": (
            Segment(0, 40, "ja", "S2"),
            Segment(45, 70, "verder", "S1"),
            Segment(70, 100, "nieuw", "A"),
        ),
        # 110–170: all three by name.
        b"three": (Segment(0, 30, "nieuw", "S3"), Segment(30, 60, "slot", "S1")),
    }

    async def fake(config, clip, *, known=(), **_kw):  # noqa: ANN001, ANN003
        asked.append([k.name for k in known])
        segs = answers[clip.data]
        return Transcript(" ".join(s.text for s in segs), 100, segs)

    monkeypatch.setattr(pipeline, "provider_transcribe", fake)
    result: TranscribedRecording = await pipeline.transcribe_recording(
        _config("openai", "gpt-4o-transcribe-diarize"),
        b"audio",
        "webm",
        language="nl",
        duration_seconds=170,
    )
    assert asked == [[], ["S1", "S2"], ["S1", "S2", "S3"]]
    assert result.parts == 3 and result.aligned and result.voiced
    assert [(r["speaker"], r["text"]) for r in result.segments] == [
        ("S1", "hallo"),
        ("S2", "ja"),
        ("S1", "verder"),
        ("S3", "nieuw"),
        ("S1", "slot"),
    ]


# --- a recording taken up again ---------------------------------------------------------- #
def test_sessions_fold_apart_and_join_only_with_ffmpeg(monkeypatch) -> None:
    """Each recorder session is its own file (its first piece carries a header of its own);
    one session is the recording as it always was, several are joined by ffmpeg's concat
    demuxer — and without ffmpeg the join is refused in one sentence rather than the second
    half of the meeting being silently dropped."""
    folded = pipeline.fold_sessions([[b"h1", b"a", b"b"], [b"h2", b"c"]], "webm")
    assert folded == [b"h1ab", b"h2c"]
    monkeypatch.setattr(pipeline, "ffmpeg_available", lambda: False)
    assert pipeline.concat_with_ffmpeg([b"h1ab"], "webm") == b"h1ab"
    with pytest.raises(pipeline.NeedsJoin) as refused:
        pipeline.concat_with_ffmpeg(folded, "webm")
    assert refused.value.message_key == "meetings.error.needs_join"


def test_the_transcript_document_says_where_the_recording_was_interrupted() -> None:
    """A joined recording has one continuous clock with minutes missing from it; the model
    reads a line of its own where each gap falls, never two sentences that pretend to follow
    each other."""
    document, _cut = transcript_document(
        title="t",
        occurred_at=datetime(2026, 9, 25, 9, 0),
        kind="physical",
        segments=[
            {"start": 0.0, "end": 5.0, "speaker": "S1", "text": "voor"},
            {"start": 5.0, "end": 9.0, "speaker": "S2", "text": "na"},
        ],
        text="voor na",
        gaps=[{"at": 5.0, "seconds": 92.4}],
    )
    payload = json.loads(document)
    assert payload["recording_interrupted"] is True
    assert [
        (line["at"], line.get("recording_interrupted_seconds")) for line in payload["transcript"]
    ] == [
        (0.0, None),
        (5.0, 92),
        (5.0, None),
    ]
