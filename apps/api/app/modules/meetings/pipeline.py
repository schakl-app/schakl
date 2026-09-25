"""The recording → the words: folding the pieces, cutting to what a provider takes, transcribing.

Five rules, each of them the reason a step exists.

**The browser never depends on a provider's limit.** It uploads one piece a minute while it
records (``MeetingChunk``), and this file folds them: the pieces of one ``MediaRecorder``
session are one file when byte-concatenated in order, because only the first carries the
container header and every later one is a continuation. So the whole recording exists nowhere
until the worker makes it, which is what lets a crashed tab lose a minute rather than a meeting.

**A capture the phone takes away is taken up again, and the recording is one file.** A locked
screen, a frozen tab or an incoming call ends a ``MediaRecorder`` without anybody pressing stop;
the recorder starts a new one on the same meeting (``recorder.svelte.ts``), and that one opens
with a container header of its own, so its pieces are a second *session* and cannot be
byte-appended to the first. :func:`fold_sessions` folds each session on its own and joins them
with ffmpeg's concat demuxer — a copy, never a re-encode — so the row still names one recording,
the player one timeline and the transcript one clock; what the interruption cost is measured
from the pieces' own arrival times and lengths (``jobs._fold``) and stated on the row
(``Meeting.recording_gaps``), never guessed from a browser's clock.

**A recording is one request where the provider allows, and cut into parts only where it does
not** — and the difference is not cosmetic: a provider labels speakers *per request*, so "S1" in
part two is not "S1" in part one. Two things keep a person one label across a cut, and they
are used together. **The provider is handed the voices it has already heard**
(:func:`reference_clips`): where the model takes known-speaker references (OpenAI's diarize
model, four of them), every part after the first carries a short sample of each voice the
earlier parts labelled, cut from the recording itself, and the model answers those voices *by
the label given* — a match by voice, by the one party that can hear one. **And the parts
overlap** (:data:`OVERLAP_SECONDS`): every part but the first starts before the previous one
ended, both parts transcribe the same stretch, and a label the provider did not name is
*matched* to the label in the old part that spoke during the same seconds
(:func:`align_labels`) — a match by time, only where the two labels' speech overlaps enough to
be the same person and nobody else's. The overlap was the whole mechanism once, and on a
hundred-minute meeting with three people it kept only the person who never stopped talking:
in forty-five seconds the other two had said too little, at boundaries two independent
transcriptions did not draw in the same place, to clear the bar — thirteen labels for three
people, numbered up to seventeen. A label neither mechanism pairs keeps a fresh number, so the
failure direction stays the old one (a speaker split in two, said on the screen), never two
people merged into one; and the labels are renumbered densely at the end
(:func:`compact_labels`), so a label whose every row fell inside an overlap leaves no hole for
the screen to count. Where the provider answers no timestamps at all there is nothing to align
on, so those parts are cut edge to edge as before. That is still the argument for a provider
that takes three hours in one request (Voxtral): an alignment is an inference, one request is
a fact.

**Cutting needs ffmpeg, and its absence is said in one sentence.** A part is cut on a frame
boundary with ``-c copy`` (Opus and AAC are frame-independent, so a copy cut is exact to a frame),
and there is no honest way to do that in pure Python for every container a browser or a phone
produces. ``ffmpeg`` ships in the API image; a dev box without it answers
``meetings.error.needs_split`` for exactly the recordings that would have needed it, and none of
the ones that would not.

**A browser's recording has no length until something writes one.** ``MediaRecorder`` streams a
WebM whose header says *unknown duration* and carries no cues, because the file was still being
written when the header went out — so ``<audio>`` reports ``Infinity``, draws no total and
cannot be scrubbed. :func:`remux` rewrites the folded file once, copying every packet, so the
stored recording states its length and is seekable; where ffmpeg is absent the bytes are stored
as they came and the page falls back to the browser's own workaround.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.ai.audio import AudioClip
from app.core.ai.providers import ProviderConfig
from app.core.ai.transcribe import (
    KnownSpeaker,
    Segment,
    SpeechLimits,
    Transcript,
    speech_limits,
)
from app.core.ai.transcribe import transcribe as provider_transcribe
from app.errors import AppError

logger = logging.getLogger("schakl.meetings")

#: The folded recording's ceiling — two hours of 32 kbit/s Opus is ~29 MB, a phone's AAC memo
#: of the same length ~120 MB; this admits either with room, and refuses a video file somebody
#: dropped by mistake. Over it is an error, never a truncation (``core/impex/parsing.py``).
MAX_RECORDING_BYTES = 512 * 1024 * 1024
#: A part is cut a little under the provider's cap so a cut *at* the cap never lands over it.
_PART_MARGIN = 0.97
#: How much of the previous part every later part repeats, so a speaker's label can be matched
#: across the cut by the seconds they were speaking in both transcriptions. Ninety seconds:
#: forty-five was enough for the two people of a two-way exchange and not for the third and
#: fourth person of a meeting, who may not say a word in three quarters of a minute; the cost
#: is under six per cent of a twenty-five-minute part, transcribed twice.
OVERLAP_SECONDS = 90
#: The least shared speaking time that counts as "the same person": two labels whose speech
#: overlaps for less than this inside the window may be a cross-talk artefact.
MIN_MATCH_SECONDS = 2.0
#: …and the matched seconds must be at least this share of the new label's time in the window,
#: or the new label mostly spoke while the old one did not — a different person.
MIN_MATCH_SHARE = 0.5
#: A voice sample for the provider is cut this far inside the segment it comes from, so the
#: copy cut's frame rounding never reaches the neighbour's words, and is at most this long.
_REFERENCE_PAD_SECONDS = 0.25
_REFERENCE_TARGET_SECONDS = 8.0
#: The content type per sniffed extension (``core/ai/audio._MAGIC``'s vocabulary).
CONTENT_TYPES: dict[str, str] = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
}
#: A part shorter than this is the cut's rounding, not speech: cutting 1386 s at 693 s writes
#: 693.008 + 693.007 + a 0.027 s third file, and that sliver went to the provider as a request
#: of its own. Dropped rather than sent — it holds no word anybody said.
_MIN_PART_SECONDS = 1.0
#: What ffmpeg is told to write per container.
_FORMATS: dict[str, str] = {
    "webm": "webm",
    "ogg": "ogg",
    "m4a": "mp4",
    "wav": "wav",
    "mp3": "mp3",
}
#: The containers a browser's ``MediaRecorder`` streams without a duration; the others come
#: from a file that was finished when it was written.
_REMUX_EXTENSIONS = frozenset({"webm", "ogg"})


@dataclass(frozen=True)
class Part:
    """One request's worth of audio, and where it starts in the whole recording."""

    data: bytes
    offset_seconds: float
    #: How long the part is, where the cut measured it; ``None`` for a recording sent whole.
    length_seconds: float | None = None


class NeedsSplit(AppError):
    """The recording is over the provider's cap and ffmpeg is not here to cut it."""

    def __init__(self) -> None:
        super().__init__("meetings_needs_split", "meetings.error.needs_split", status_code=422)


class NeedsJoin(AppError):
    """The recording was taken up again after an interruption and ffmpeg is not here to join
    the sessions — or refused to."""

    def __init__(self, key: str = "meetings.error.needs_join") -> None:
        super().__init__("meetings_needs_join", key, status_code=422)


def fold_chunks(chunks: list[bytes]) -> bytes:
    """The pieces of one recorder session, in order, are the file."""
    total = sum(len(c) for c in chunks)
    if total > MAX_RECORDING_BYTES:
        raise AppError("meetings_too_large", "meetings.error.too_large", status_code=413)
    return b"".join(chunks)


def fold_sessions(sessions: list[list[bytes]], extension: str) -> list[bytes]:
    """Every recorder session folded to its own file, in order. One session is the common
    case and folds as before; the caller joins several with :func:`concat_with_ffmpeg`."""
    if not sessions:
        raise AppError("meetings_no_audio", "meetings.error.no_audio", status_code=422)
    folded = [fold_chunks(chunks) for chunks in sessions if chunks]
    if sum(len(f) for f in folded) > MAX_RECORDING_BYTES:
        raise AppError("meetings_too_large", "meetings.error.too_large", status_code=413)
    return folded


def parts_needed(total_bytes: int, duration_seconds: int | None, limits: SpeechLimits) -> int:
    """How many requests this recording is, for this provider. One when it fits."""
    by_bytes = math.ceil(total_bytes / (limits.max_bytes * _PART_MARGIN))
    by_seconds = 1
    if limits.max_seconds is not None and duration_seconds:
        by_seconds = math.ceil(duration_seconds / (limits.max_seconds * _PART_MARGIN))
    return max(1, by_bytes, by_seconds)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe_duration(path: Path) -> float | None:
    out = subprocess.run(  # noqa: S603 - our own arguments, our own file
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    match = re.search(r"[\d.]+", out.stdout or "")
    return float(match.group(0)) if match else None


def probe_duration(data: bytes, extension: str) -> float | None:
    """How long a recording is, asked of the file itself. ``None`` without ffmpeg."""
    if not ffmpeg_available():
        return None
    with tempfile.NamedTemporaryFile(suffix=f".{extension}", prefix="schakl-meeting-") as tmp:
        tmp.write(data)
        tmp.flush()
        return _probe_duration(Path(tmp.name))


def remux(data: bytes, extension: str) -> bytes | None:
    """Rewrite a browser's streamed container so it states its length and is seekable.

    A copy, never a re-encode: every packet is carried over and only the container's header and
    index are written again. ``None`` where nothing needed doing (a container that was finished
    when it was written, or no ffmpeg) or where ffmpeg refused, in which case the caller keeps
    the bytes as they came — a recording that plays without a scrub bar beats none.
    """
    if extension not in _REMUX_EXTENSIONS or not ffmpeg_available():
        return None
    with tempfile.TemporaryDirectory(prefix="schakl-meeting-") as tmp:
        source = Path(tmp) / f"in.{extension}"
        target = Path(tmp) / f"out.{extension}"
        source.write_bytes(data)
        result = subprocess.run(  # noqa: S603
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-c",
                "copy",
                "-f",
                _FORMATS[extension],
                str(target),
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0 or not target.exists():
            logger.warning("meetings: remux refused: %s", result.stderr[-400:])
            return None
        return target.read_bytes()


def concat_with_ffmpeg(sessions: list[bytes], extension: str) -> bytes:
    """Join the folded sessions of one recording into one file, copying every packet.

    The concat *demuxer* (not the protocol): each session is a complete container of its own —
    the browser wrote a header for every ``MediaRecorder`` it started — and byte-appending
    containers is not a file. Same codec, same parameters (one recorder, one bitrate), so a
    copy is exact and the timestamps are made continuous by the muxer. Refused where ffmpeg is
    absent or declines, in one sentence each: a recording that reaches the provider as its
    first session alone would be a recording silently missing its second half.
    """
    if len(sessions) == 1:
        return sessions[0]
    if not ffmpeg_available():
        raise NeedsJoin()
    fmt = _FORMATS.get(extension, extension)
    with tempfile.TemporaryDirectory(prefix="schakl-meeting-") as tmp:
        root = Path(tmp)
        listing = root / "sessions.txt"
        lines = []
        for index, data in enumerate(sessions):
            path = root / f"session{index:03d}.{extension}"
            path.write_bytes(data)
            lines.append(f"file '{path}'")
        listing.write_text("\n".join(lines) + "\n")
        target = root / f"joined.{extension}"
        result = subprocess.run(  # noqa: S603
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-map",
                "0:a:0",
                "-c",
                "copy",
                "-f",
                fmt,
                str(target),
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0 or not target.exists():
            logger.warning("meetings: joining the sessions refused: %s", result.stderr[-400:])
            raise NeedsJoin("meetings.error.join_failed")
        return target.read_bytes()


def plan_windows(duration: float, part_seconds: int, overlap: int) -> list[tuple[float, float]]:
    """Where each part starts and how long it is: ``[(start, length), …]``.

    Every part but the first starts ``overlap`` seconds before the previous one ends, so the
    stride is ``part_seconds - overlap``; the last part is cut to what is left. A trailing
    sliver shorter than :data:`_MIN_PART_SECONDS` past the previous part's end is not a part.
    """
    stride = max(1, part_seconds - overlap)
    windows: list[tuple[float, float]] = []
    start = 0.0
    while True:
        length = min(float(part_seconds), duration - start)
        windows.append((start, length))
        end = start + length
        if end >= duration - _MIN_PART_SECONDS:
            break
        start += stride
    return windows


def cut_windows(
    data: bytes, extension: str, windows: list[tuple[float, float]]
) -> list[Part | None]:
    """Cut the recording into the given windows on frame boundaries, without re-encoding —
    one entry per window, in order, ``None`` where ffmpeg wrote nothing.

    One ffmpeg run per window rather than the segment muxer, because overlapping windows are
    not something a segmenter can write. ``-ss`` before ``-i`` seeks in the demuxer, and with
    ``-c copy`` the cut lands on the first packet at or after the mark — for Opus and AAC every
    packet stands alone, so the cut is exact to a frame.
    """
    fmt = _FORMATS.get(extension, extension)
    with tempfile.TemporaryDirectory(prefix="schakl-meeting-") as tmp:
        source = Path(tmp) / f"in.{extension}"
        source.write_bytes(data)
        cuts: list[Part | None] = []
        for index, (start, length) in enumerate(windows):
            target = Path(tmp) / f"part{index:04d}.{extension}"
            result = subprocess.run(  # noqa: S603
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    f"{start:.3f}",
                    "-t",
                    f"{length:.3f}",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-c",
                    "copy",
                    "-avoid_negative_ts",
                    "make_zero",
                    "-f",
                    fmt,
                    str(target),
                ],
                capture_output=True,
                timeout=600,
                check=False,
            )
            if result.returncode != 0 or not target.exists():
                logger.warning("meetings: cut at %.1f s refused: %s", start, result.stderr[-400:])
                cuts.append(None)
                continue
            measured = _probe_duration(target)
            cuts.append(
                Part(
                    data=target.read_bytes(),
                    offset_seconds=start,
                    length_seconds=measured if measured is not None else length,
                )
            )
        return cuts


def cut_with_ffmpeg(data: bytes, extension: str, windows: list[tuple[float, float]]) -> list[Part]:
    """The recording as the parts its windows describe. A window ffmpeg could not cut is an
    error (a recording that cannot be cut cannot be transcribed in parts); a trailing sliver
    shorter than :data:`_MIN_PART_SECONDS` is the cut's rounding and is dropped."""
    parts: list[Part] = []
    for cut in cut_windows(data, extension, windows):
        if cut is None:
            raise AppError("meetings_cut_failed", "meetings.error.failed", status_code=422)
        if parts and cut.length_seconds is not None and cut.length_seconds < _MIN_PART_SECONDS:
            continue
        parts.append(cut)
    return parts


def plan_parts(
    data: bytes, extension: str, duration_seconds: int | None, limits: SpeechLimits
) -> list[Part]:
    """The recording as the list of requests it will take. One part is the common case.

    A cut recording is cut with an overlap wherever the provider answers timestamps — that is
    what the labels are aligned on afterwards (:func:`align_labels`). A provider that answers
    text only gets edge-to-edge parts: an overlap it could not be told apart from would be the
    same minute transcribed twice.
    """
    n = parts_needed(len(data), duration_seconds, limits)
    if n == 1:
        return [Part(data=data, offset_seconds=0.0)]
    if not ffmpeg_available():
        raise NeedsSplit()
    duration = float(duration_seconds or 0)
    if not duration:
        duration = probe_duration(data, extension) or 0.0
        if duration:
            n = parts_needed(len(data), int(math.ceil(duration)), limits)
            if n == 1:
                return [Part(data=data, offset_seconds=0.0)]
    if not duration:
        raise NeedsSplit()
    overlap = OVERLAP_SECONDS if limits.timestamps else 0
    cap = int(limits.max_seconds * _PART_MARGIN) if limits.max_seconds is not None else None
    # Rounded *up*, with a second to spare: rounding down left a sliver past the last cut. The
    # overlap is paid for by asking for one more part where the cap leaves no room for it.
    while True:
        part_seconds = max(60, math.ceil((duration + (n - 1) * overlap) / n) + 1)
        if cap is None or part_seconds <= cap:
            break
        part_seconds = cap
        if n * part_seconds - (n - 1) * overlap >= duration:
            break
        n += 1
    return cut_with_ffmpeg(data, extension, plan_windows(duration, part_seconds, overlap))


def _label_order(segments: list[dict]) -> list[str]:
    """Labels by first appearance."""
    seen: list[str] = []
    for row in segments:
        label = row.get("speaker")
        if label and label not in seen:
            seen.append(label)
    return seen


def reference_windows(
    segments: list[dict], *, limit: int, min_seconds: float, max_seconds: float
) -> list[tuple[str, float, float]]:
    """Which stretch of the recording to hand the provider as each voice's sample:
    ``[(label, start, length), …]`` for the ``limit`` labels that spoke most, each cut from
    the longest stretch that label spoke alone.

    Most-spoken first because the provider takes a bounded number of names, and the people who
    carried the meeting so far are the ones most likely to speak in the next part; the sample
    is one segment because a segment is one label speaking, and it is cut a little inside its
    own ends so the neighbour's first word never lands in it. A label with no segment long
    enough to sample is left for the overlap to pair.
    """
    spoken: dict[str, float] = {}
    longest: dict[str, tuple[float, float]] = {}
    for row in segments:
        label = row.get("speaker")
        if not label:
            continue
        start, end = float(row["start"]), float(row["end"])
        length = max(0.0, end - start)
        spoken[label] = spoken.get(label, 0.0) + length
        if label not in longest or length > longest[label][1]:
            longest[label] = (start, length)
    order = _label_order(segments)
    ranked = sorted(spoken, key=lambda label: (-spoken[label], order.index(label)))
    windows: list[tuple[str, float, float]] = []
    for label in ranked[: max(0, limit)]:
        start, length = longest[label]
        usable = length - 2 * _REFERENCE_PAD_SECONDS
        if usable < min_seconds:
            continue
        take = min(usable, max_seconds, _REFERENCE_TARGET_SECONDS)
        windows.append((label, start + _REFERENCE_PAD_SECONDS, take))
    return windows


def reference_clips(
    data: bytes, extension: str, segments: list[dict], *, limits: SpeechLimits
) -> list[KnownSpeaker]:
    """A voice sample per label the provider should keep, cut from the recording.

    Empty where the provider takes none, where nothing has been labelled yet, or where ffmpeg
    is not here — and a clip the cut measured outside the vendor's bounds is left out rather
    than sent, because a refused request costs the whole part.
    """
    if limits.known_speakers <= 0 or not segments or not ffmpeg_available():
        return []
    windows = reference_windows(
        segments,
        limit=limits.known_speakers,
        min_seconds=limits.reference_min_seconds,
        max_seconds=limits.reference_max_seconds,
    )
    if not windows:
        return []
    cuts = cut_windows(data, extension, [(start, length) for _, start, length in windows])
    content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
    known: list[KnownSpeaker] = []
    for (label, _start, _length), cut in zip(windows, cuts, strict=True):
        if cut is None or not cut.data:
            continue
        measured = cut.length_seconds
        if measured is not None and not (
            limits.reference_min_seconds <= measured <= limits.reference_max_seconds
        ):
            continue
        known.append(
            KnownSpeaker(
                name=label,
                sample=AudioClip(data=cut.data, content_type=content_type, extension=extension),
            )
        )
    return known


_LABEL_RE = re.compile(r"\d+")


def relabel(
    segments: list[Segment],
    *,
    offset: float,
    next_label: int,
    known: frozenset[str] = frozenset(),
) -> tuple[list[dict], int]:
    """The provider's labels (``speaker_0``, ``A``, ``SPEAKER_01``) become ``S<n>`` in order of
    first appearance, numbered on from ``next_label`` so two parts never share a label; and
    every timestamp moves by the part's offset. A label in ``known`` is one the provider was
    asked to keep and did, and stays as it is. Returns the rows and the next free number."""
    mapping: dict[str, str] = {name: name for name in known}
    rows: list[dict] = []
    for seg in segments:
        label: str | None = None
        if seg.speaker is not None:
            if seg.speaker not in mapping:
                mapping[seg.speaker] = f"S{next_label}"
                next_label += 1
            label = mapping[seg.speaker]
        rows.append(
            {
                "start": round(seg.start + offset, 2),
                "end": round(seg.end + offset, 2),
                "speaker": label,
                "text": seg.text,
            }
        )
    return rows, next_label


def _overlap_seconds(a: dict, b: dict) -> float:
    latest_start = max(float(a["start"]), float(b["start"]))
    earliest_end = min(float(a["end"]), float(b["end"]))
    return max(0.0, earliest_end - latest_start)


def align_labels(
    previous: list[dict],
    incoming: list[Segment],
    *,
    offset: float,
    window_end: float,
    next_label: int,
    known: frozenset[str] = frozenset(),
) -> tuple[list[dict], int]:
    """Number the new part's speakers so that a person keeps their label across the cut.

    ``previous`` are the rows already accepted (absolute times, ``S<n>`` labels); ``incoming``
    the new part's raw segments, whose window ``[offset, window_end)`` the previous part also
    transcribed. A raw label in ``known`` is a voice the provider was handed a sample of and
    recognised: it *is* that label, and takes it before any pairing by time. For every other
    raw label, the seconds it spoke inside the window are compared with every unclaimed
    previous label's seconds there, and the pair that shares the most speaking time is taken —
    greedily, best pair first, each label used once — when the shared time is at least
    :data:`MIN_MATCH_SECONDS` and at least :data:`MIN_MATCH_SHARE` of the new label's own time
    in the window. A raw label that matches nothing gets a fresh number.

    The rows of the new part that fall inside the window are then dropped: the previous part
    already carries those words, and keeping both would read every sentence at the cut twice.
    A segment is inside the window when its midpoint is, so a sentence that straddles the
    boundary is kept exactly once, by whichever part holds more of it.
    """
    old_in_window = [
        row
        for row in previous
        if row.get("speaker") and float(row["end"]) > offset and float(row["start"]) < window_end
    ]
    absolute: list[dict] = [
        {
            "start": round(seg.start + offset, 2),
            "end": round(seg.end + offset, 2),
            "speaker": seg.speaker,
            "text": seg.text,
        }
        for seg in incoming
    ]
    new_in_window = [row for row in absolute if row["speaker"] and float(row["start"]) < window_end]
    clipped = lambda row: {  # noqa: E731 - the window is the comparison, not the row
        "start": max(float(row["start"]), offset),
        "end": min(float(row["end"]), window_end),
    }
    mapping: dict[str, str] = {name: name for name in known}
    taken: set[str] = set(known)
    shared: dict[tuple[str, str], float] = {}
    spoken: dict[str, float] = {}
    for new in new_in_window:
        if new["speaker"] in mapping:
            continue
        n = clipped(new)
        spoken[new["speaker"]] = spoken.get(new["speaker"], 0.0) + max(0.0, n["end"] - n["start"])
        for old in old_in_window:
            if old["speaker"] in taken:
                continue
            seconds = _overlap_seconds(n, clipped(old))
            if seconds > 0:
                key = (new["speaker"], old["speaker"])
                shared[key] = shared.get(key, 0.0) + seconds
    for (raw, old_label), seconds in sorted(shared.items(), key=lambda kv: -kv[1]):
        if raw in mapping or old_label in taken:
            continue
        if seconds < MIN_MATCH_SECONDS or seconds < MIN_MATCH_SHARE * spoken.get(raw, 0.0):
            continue
        mapping[raw] = old_label
        taken.add(old_label)
    rows: list[dict] = []
    for row in absolute:
        raw = row["speaker"]
        label: str | None = None
        if raw is not None:
            if raw not in mapping:
                mapping[raw] = f"S{next_label}"
                next_label += 1
            label = mapping[raw]
        midpoint = (float(row["start"]) + float(row["end"])) / 2
        if old_in_window and midpoint < window_end:
            continue
        rows.append({**row, "speaker": label})
    return rows, next_label


def compact_labels(segments: list[dict]) -> dict[str, str]:
    """Renumber the labels densely, ``S1``… in order of first appearance, in place.

    Numbering on through the parts leaves holes: a label whose every row fell inside an overlap
    was numbered and then dropped, so the screen counted "S17" over thirteen voices. Returns the
    old → new mapping, for a caller holding names against the old labels.
    """
    mapping: dict[str, str] = {}
    for label in _label_order(segments):
        mapping[label] = f"S{len(mapping) + 1}"
    if all(old == new for old, new in mapping.items()):
        return mapping
    for row in segments:
        label = row.get("speaker")
        if label:
            row["speaker"] = mapping[label]
    return mapping


@dataclass
class TranscribedRecording:
    segments: list[dict]
    text: str
    seconds: int
    parts: int
    #: Whether a cut recording's speakers were matched across the cuts (an overlap was cut and
    #: the provider answered timestamps). ``False`` for one part, and for parts the labels of
    #: which were numbered on because there was nothing to align them on.
    aligned: bool = False
    #: Whether the provider was handed the voices it had already heard, so a label across a
    #: cut is the model's own recognition of a voice rather than an inference from timing.
    voiced: bool = False


async def transcribe_recording(
    config: ProviderConfig,
    data: bytes,
    extension: str,
    *,
    language: str | None,
    duration_seconds: int | None,
) -> TranscribedRecording:
    """The whole recording, as one request or several, into one transcript.

    Blocking work (the cut) runs in a thread; every provider call is awaited in turn — parts
    are serial on purpose, so a two-hour recording never opens six connections to a provider
    whose per-user rate limit is exactly the thing that would refuse them — and because each
    part after the first is handed the voices the parts before it labelled.
    """
    limits = speech_limits(config)
    parts = await asyncio.to_thread(plan_parts, data, extension, duration_seconds, limits)
    content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
    segments: list[dict] = []
    texts: list[str] = []
    seconds = 0
    next_label = 1
    aligned = False
    voiced = False
    previous_end = 0.0
    known: list[KnownSpeaker] = []
    for index, part in enumerate(parts):
        clip = AudioClip(data=part.data, content_type=content_type, extension=extension)
        result: Transcript = await provider_transcribe(
            config,
            clip,
            language=language,
            diarize=limits.diarize,
            timestamps=limits.timestamps,
            known=known,
        )
        names = frozenset(k.name for k in known)
        voiced = voiced or bool(known)
        overlapping = index > 0 and previous_end > part.offset_seconds and bool(result.segments)
        if overlapping:
            rows, next_label = align_labels(
                segments,
                list(result.segments),
                offset=part.offset_seconds,
                window_end=previous_end,
                next_label=next_label,
                known=names,
            )
            aligned = True
        else:
            rows, next_label = relabel(
                list(result.segments),
                offset=part.offset_seconds,
                next_label=next_label,
                known=names,
            )
        segments.extend(rows)
        if result.text and not overlapping:
            texts.append(result.text)
        seconds += result.seconds
        if part.length_seconds is not None:
            previous_end = part.offset_seconds + part.length_seconds
        else:
            previous_end = max((float(r["end"]) for r in rows), default=part.offset_seconds)
        if index < len(parts) - 1 and limits.known_speakers > 0:
            known = await asyncio.to_thread(
                reference_clips, data, extension, segments, limits=limits
            )
    if len(parts) > 1:
        compact_labels(segments)
    if aligned:
        # The provider's flat text would repeat every overlap; the rows are the words now, and
        # the metered seconds count only what was really recorded once.
        text = " ".join(str(r["text"]).strip() for r in segments if str(r["text"]).strip())
        seconds = min(seconds, int(math.ceil(duration_seconds or seconds)))
    else:
        text = "\n".join(texts).strip()
    if seconds == 0 and duration_seconds:
        seconds = duration_seconds
    return TranscribedRecording(
        segments=segments,
        text=text,
        seconds=seconds,
        parts=len(parts),
        aligned=aligned,
        voiced=voiced,
    )


__all__ = [
    "CONTENT_TYPES",
    "MAX_RECORDING_BYTES",
    "MIN_MATCH_SECONDS",
    "OVERLAP_SECONDS",
    "NeedsJoin",
    "NeedsSplit",
    "Part",
    "TranscribedRecording",
    "align_labels",
    "compact_labels",
    "concat_with_ffmpeg",
    "cut_windows",
    "cut_with_ffmpeg",
    "fold_chunks",
    "fold_sessions",
    "parts_needed",
    "plan_parts",
    "plan_windows",
    "probe_duration",
    "reference_clips",
    "reference_windows",
    "relabel",
    "remux",
    "transcribe_recording",
]
