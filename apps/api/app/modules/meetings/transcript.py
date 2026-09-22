"""The transcript as a document: one shape for agents, four file formats for people.

``GET /meetings/{id}/transcript`` answers JSON by default — the whole transcript with every
speaker label resolved to a name, which is what an MCP client asks for — and, on ``format=``,
the same words as a plain-text, markdown, SubRip or WebVTT file. One route, because the export
button and the generated tool are the same question asked by two callers, and two routes are
two answers that drift.
"""

from __future__ import annotations

from datetime import datetime

from app.modules.meetings.schemas import MeetingTranscript, TranscriptSegment

FORMATS: tuple[str, ...] = ("json", "txt", "md", "srt", "vtt")
MEDIA_TYPES: dict[str, str] = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
}


def clock(seconds: float | None) -> str:
    """``0:07``, ``12:40``, ``1:02:15`` — the screen's own clock, for a heading or a line."""
    whole = int(max(0.0, float(seconds or 0)))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _stamp(seconds: float, *, sep: str) -> str:
    """``HH:MM:SS,mmm`` (SubRip) or ``HH:MM:SS.mmm`` (WebVTT)."""
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def _name(segment: TranscriptSegment, speakers: dict[str, str]) -> str | None:
    if not segment.speaker:
        return None
    return speakers.get(segment.speaker) or segment.speaker


def _cue_lines(
    segments: list[TranscriptSegment], speakers: dict[str, str], *, sep: str, numbered: bool
) -> list[str]:
    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        end = segment.end if segment.end > segment.start else segment.start + 1.0
        if numbered:
            lines.append(str(index))
        lines.append(f"{_stamp(segment.start, sep=sep)} --> {_stamp(end, sep=sep)}")
        name = _name(segment, speakers)
        lines.append(f"{name}: {segment.text}" if name else segment.text)
        lines.append("")
    return lines


def _header(transcript: MeetingTranscript) -> list[str]:
    when = transcript.occurred_at
    day = when.strftime("%d-%m-%Y %H:%M") if isinstance(when, datetime) else str(when)
    return [transcript.title, day]


def render_transcript(transcript: MeetingTranscript, fmt: str) -> str:
    """The transcript in one of the text formats. ``json`` is not here: the model *is* JSON."""
    speakers = transcript.speakers
    segments = transcript.segments
    if fmt == "srt":
        return "\n".join(_cue_lines(segments, speakers, sep=",", numbered=True)).strip() + "\n"
    if fmt == "vtt":
        body = _cue_lines(segments, speakers, sep=".", numbered=False)
        return "WEBVTT\n\n" + "\n".join(body).strip() + "\n"
    if fmt == "md":
        lines = [f"# {transcript.title}", "", _header(transcript)[1], ""]
        if not segments:
            lines.append(transcript.text)
        for segment in segments:
            name = _name(segment, speakers)
            who = f"**{name}:** " if name else ""
            lines.append(f"`{clock(segment.start)}` {who}{segment.text}  ")
        return "\n".join(lines).strip() + "\n"
    # txt
    lines = [*_header(transcript), ""]
    if not segments:
        lines.append(transcript.text)
    for segment in segments:
        name = _name(segment, speakers)
        who = f"{name}: " if name else ""
        lines.append(f"[{clock(segment.start)}] {who}{segment.text}")
    return "\n".join(lines).strip() + "\n"


def transcript_filename(title: str, fmt: str) -> str:
    safe = (
        "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in title).strip() or "transcript"
    )
    return f"{safe[:80]}.{fmt}"


__all__ = ["FORMATS", "MEDIA_TYPES", "clock", "render_transcript", "transcript_filename"]
