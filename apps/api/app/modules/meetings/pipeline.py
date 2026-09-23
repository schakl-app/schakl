"""The recording → the words: folding the pieces, cutting to what a provider takes, transcribing.

Three rules, each of them the reason a step exists.

**The browser never depends on a provider's limit.** It uploads one piece a minute while it
records (``MeetingChunk``), and this file folds them: the pieces of one ``MediaRecorder``
session are one file when byte-concatenated in order, because only the first carries the
container header and every later one is a continuation. So the whole recording exists nowhere
until the worker makes it, which is what lets a crashed tab lose a minute rather than a meeting.

**A recording is one request where the provider allows, and cut into parts only where it does
not** — and the difference is not cosmetic: a provider labels speakers *per request*, so "S1" in
part two is not "S1" in part one. The labels are therefore numbered on through the parts (S1, S2
in the first; S3, S4 in the second) and never merged by guesswork; the review screen names them.
That is the whole argument for a provider that takes three hours in one request (Voxtral) over
one that takes twenty-five minutes, and :func:`plan_parts` is where the numbers are read.

**Cutting needs ffmpeg, and its absence is said in one sentence.** A part is cut on a cluster
boundary with ``-c copy`` (Opus and AAC are frame-independent, so a copy cut is exact to a frame),
and there is no honest way to do that in pure Python for every container a browser or a phone
produces. ``ffmpeg`` ships in the API image; a dev box without it answers
``meetings.error.needs_split`` for exactly the recordings that would have needed it, and none of
the ones that would not.
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
#: The content type per sniffed extension (``core/ai/audio._MAGIC``'s vocabulary).
CONTENT_TYPES: dict[str, str] = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
}
#: A part shorter than this is the segment muxer's rounding, not speech: cutting 1386 s at
#: 693 s writes 693.008 + 693.007 + a 0.027 s third file, and that sliver went to the provider
#: as a request of its own. Dropped rather than sent — it holds no word anybody said.
_MIN_PART_SECONDS = 1.0
#: What ffmpeg's segment muxer is told to write per container.
_SEGMENT_FORMATS: dict[str, str] = {
    "webm": "webm",
    "ogg": "ogg",
    "m4a": "mp4",
    "wav": "wav",
    "mp3": "mp3",
}


@dataclass(frozen=True)
class Part:
    """One request's worth of audio, and where it starts in the whole recording."""

    data: bytes
    offset_seconds: float


class NeedsSplit(AppError):
    """The recording is over the provider's cap and ffmpeg is not here to cut it."""

    def __init__(self) -> None:
        super().__init__("meetings_needs_split", "meetings.error.needs_split", status_code=422)


def fold_chunks(chunks: list[bytes]) -> bytes:
    """The pieces of one recorder session, in order, are the file."""
    total = sum(len(c) for c in chunks)
    if total > MAX_RECORDING_BYTES:
        raise AppError("meetings_too_large", "meetings.error.too_large", status_code=413)
    return b"".join(chunks)


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


def split_with_ffmpeg(data: bytes, extension: str, part_seconds: int) -> list[Part]:
    """Cut on a frame boundary without re-encoding; parts start at zero and carry their offset."""
    fmt = _SEGMENT_FORMATS.get(extension, extension)
    with tempfile.TemporaryDirectory(prefix="schakl-meeting-") as tmp:
        source = Path(tmp) / f"in.{extension}"
        source.write_bytes(data)
        pattern = Path(tmp) / f"part%04d.{extension}"
        subprocess.run(  # noqa: S603
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(source),
                "-f",
                "segment",
                "-segment_time",
                str(part_seconds),
                "-segment_format",
                fmt,
                "-reset_timestamps",
                "1",
                "-c",
                "copy",
                str(pattern),
            ],
            capture_output=True,
            timeout=600,
            check=True,
        )
        parts: list[Part] = []
        offset = 0.0
        for path in sorted(Path(tmp).glob(f"part*.{extension}")):
            # The cut is at the first frame at or after the mark, so the next part starts where
            # this one measured — ask rather than assume ``part_seconds``.
            measured = _probe_duration(path)
            if parts and measured is not None and measured < _MIN_PART_SECONDS:
                continue
            parts.append(Part(data=path.read_bytes(), offset_seconds=offset))
            offset += measured if measured else float(part_seconds)
        return parts


def plan_parts(
    data: bytes, extension: str, duration_seconds: int | None, limits: SpeechLimits
) -> list[Part]:
    """The recording as the list of requests it will take. One part is the common case."""
    n = parts_needed(len(data), duration_seconds, limits)
    if n == 1:
        return [Part(data=data, offset_seconds=0.0)]
    if not ffmpeg_available():
        raise NeedsSplit()
    duration = float(duration_seconds or 0)
    if not duration:
        with tempfile.NamedTemporaryFile(suffix=f".{extension}", prefix="schakl-meeting-") as tmp:
            tmp.write(data)
            tmp.flush()
            duration = _probe_duration(Path(tmp.name)) or 0.0
        if duration:
            n = parts_needed(len(data), int(math.ceil(duration)), limits)
            if n == 1:
                return [Part(data=data, offset_seconds=0.0)]
    if not duration:
        raise NeedsSplit()
    # Rounded *up*, with a second to spare: rounding down left a sliver past the last cut.
    part_seconds = max(60, math.ceil(duration / n) + 1)
    if limits.max_seconds is not None:
        part_seconds = min(part_seconds, int(limits.max_seconds * _PART_MARGIN))
    return split_with_ffmpeg(data, extension, part_seconds)


_LABEL_RE = re.compile(r"\d+")


def relabel(segments: list[Segment], *, offset: float, next_label: int) -> tuple[list[dict], int]:
    """The provider's labels (``speaker_0``, ``A``, ``SPEAKER_01``) become ``S<n>`` in order of
    first appearance, numbered on from ``next_label`` so two parts never share a label; and
    every timestamp moves by the part's offset. Returns the rows and the next free number."""
    mapping: dict[str, str] = {}
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


@dataclass
class TranscribedRecording:
    segments: list[dict]
    text: str
    seconds: int
    parts: int


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
    whose per-user rate limit is exactly the thing that would refuse them.
    """
    limits = speech_limits(config)
    parts = await asyncio.to_thread(plan_parts, data, extension, duration_seconds, limits)
    content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
    segments: list[dict] = []
    texts: list[str] = []
    seconds = 0
    next_label = 1
    for part in parts:
        clip = AudioClip(data=part.data, content_type=content_type, extension=extension)
        result: Transcript = await provider_transcribe(
            config, clip, language=language, diarize=limits.diarize, timestamps=limits.timestamps
        )
        rows, next_label = relabel(
            list(result.segments), offset=part.offset_seconds, next_label=next_label
        )
        segments.extend(rows)
        if result.text:
            texts.append(result.text)
        seconds += result.seconds
    if seconds == 0 and duration_seconds:
        seconds = duration_seconds
    return TranscribedRecording(
        segments=segments, text="\n".join(texts).strip(), seconds=seconds, parts=len(parts)
    )


__all__ = [
    "CONTENT_TYPES",
    "MAX_RECORDING_BYTES",
    "NeedsSplit",
    "Part",
    "TranscribedRecording",
    "fold_chunks",
    "parts_needed",
    "plan_parts",
    "relabel",
    "split_with_ffmpeg",
    "transcribe_recording",
]
