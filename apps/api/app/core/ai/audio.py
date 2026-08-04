"""Reading a recorded clip before anything downstream trusts it (#246).

Modelled on ``app/core/impex/parsing.py``, and for the same reasons: the bytes arrive from a
browser, so **every cap is checked before the work it bounds**, the format comes from the
content rather than a filename the client chose, and over a limit is an error, never a
truncation. Silently transcribing the first ten seconds of a forty-second entry is the worst
outcome available, because it looks like it worked.

The container is sniffed rather than declared because the upload rides as base64 inside JSON
(the AI proxy is JSON-only), so there is no multipart filename to read and no reason to believe
a client-supplied MIME type.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from app.errors import AppError

#: A quick-add line is a sentence. 60 s of Opus is ~120 kB; the cap is generous against that
#: and still far below anything that would tie up a worker.
MAX_AUDIO_BYTES = 8 * 1024 * 1024
#: Base64 inflates by 4/3; reject the *encoded* size first so we never decode 40 MB to find out
#: it was too big.
MAX_ENCODED_CHARS = (MAX_AUDIO_BYTES * 4) // 3 + 1024

#: What a browser's MediaRecorder actually produces, plus the two the speech APIs prefer.
#: Keyed by the leading bytes that identify the container.
_MAGIC: tuple[tuple[bytes, int, str, str], ...] = (
    # WebM/Matroska (Chrome, Firefox): EBML header.
    (b"\x1a\x45\xdf\xa3", 0, "audio/webm", "webm"),
    # Ogg (Firefox): "OggS".
    (b"OggS", 0, "audio/ogg", "ogg"),
    # MP4/M4A (Safari): "ftyp" at offset 4.
    (b"ftyp", 4, "audio/mp4", "m4a"),
    # WAV: "RIFF" .... "WAVE".
    (b"RIFF", 0, "audio/wav", "wav"),
    # MP3: ID3 tag or a frame sync.
    (b"ID3", 0, "audio/mpeg", "mp3"),
    (b"\xff\xfb", 0, "audio/mpeg", "mp3"),
)


@dataclass(frozen=True)
class AudioClip:
    """A clip that has passed every check, ready to be posted to a speech provider."""

    data: bytes
    content_type: str
    #: Extension for the multipart filename — some providers route on it.
    extension: str

    @property
    def filename(self) -> str:
        return f"clip.{self.extension}"


def _sniff(data: bytes) -> tuple[str, str]:
    for magic, offset, content_type, extension in _MAGIC:
        if data[offset : offset + len(magic)] == magic:
            return content_type, extension
    raise AppError(
        "validation",
        "errors.ai_audio_unsupported",
        status_code=422,
        fields={"audio": "errors.ai_audio_unsupported"},
    )


def decode_clip(encoded: str) -> AudioClip:
    """Base64 in, a validated clip out.

    Order matters and is the whole point: encoded length, then decode, then decoded length,
    then format. Checking the decoded size first would mean decoding an arbitrarily large
    payload in order to reject it.
    """
    if not encoded:
        raise AppError("validation", "errors.validation", status_code=422)
    if len(encoded) > MAX_ENCODED_CHARS:
        raise AppError("validation", "errors.ai_audio_too_large", status_code=413)
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError("validation", "errors.validation", status_code=422) from exc
    if not data:
        raise AppError("validation", "errors.validation", status_code=422)
    if len(data) > MAX_AUDIO_BYTES:
        raise AppError("validation", "errors.ai_audio_too_large", status_code=413)
    content_type, extension = _sniff(data)
    return AudioClip(data=data, content_type=content_type, extension=extension)


__all__ = ["MAX_AUDIO_BYTES", "AudioClip", "decode_clip"]
