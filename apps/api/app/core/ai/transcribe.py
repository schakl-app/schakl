"""Speech-to-text against the tenant's own provider (#246, meetings).

Deliberately a **third top-level provider function** rather than a branch inside
``stream_chat``: the request is multipart and the response is one JSON object, so it shares
neither the SSE reader nor the event normalisation that the whole of ``providers.py`` is built
around. Forcing it through there would mean special-casing every layer for a call that has none
of the same shape.

Three providers speak one wire shape — ``POST {base}/audio/transcriptions``, multipart, Bearer —
and differ in what they answer with. **Anthropic has no speech endpoint**, which is exactly why
the speech credential is configured separately (``AISettings.speech_*``): an org can keep Claude
for writing and still dictate.

A meeting is what made the *differences* matter. A dictation is one clause and wants the words;
a meeting is two hours and wants **who said what, when** — so the function grew ``diarize`` and
``timestamps`` and answers :class:`Transcript.segments`, and :func:`speech_limits` states what a
provider will take in one request, because the answer decides whether a recording is one call
or is cut into parts first (``app/modules/meetings/pipeline.py``). Those numbers are the
vendors' own and are worth stating with their source:

* OpenAI takes **25 MB** per file, and ``gpt-4o-transcribe`` / ``-diarize`` stop at **1500
  seconds** (twenty-five minutes) however small the file — ``whisper-1`` has no duration cap.
  Only the ``-diarize`` model labels speakers (``diarized_json``); the others answer text.
* Mistral's Voxtral Transcribe 2 takes **three hours** in one request with speaker
  diarization and segment timestamps, in Dutch — which is what makes it the provider this
  product recommends for meetings: a recording that fits one request keeps one set of speaker
  labels, and a split one cannot (see the pipeline).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.ai.audio import AudioClip
from app.core.ai.providers import (
    OPENAI_BASE_URL,
    AIProviderError,
    ProviderConfig,
    _raise_for_status,
    client,
)

#: Providers whose HTTP API we know how to ask for a transcript.
TRANSCRIBING_PROVIDERS: frozenset[str] = frozenset({"openai", "openai_compatible", "mistral"})
#: A sensible starting point; the tenant may type anything their server offers.
DEFAULT_SPEECH_MODEL = "whisper-1"
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
#: Per speech provider — ``openai_compatible`` has none, the server defines its models.
DEFAULT_SPEECH_MODELS: dict[str, str] = {
    "openai": DEFAULT_SPEECH_MODEL,
    "mistral": "voxtral-mini-latest",
}

#: OpenAI's ceiling per file; the dictation cap (``audio.MAX_AUDIO_BYTES``) sits just under it.
_OPENAI_MAX_BYTES = 24 * 1024 * 1024
#: ``gpt-4o-transcribe`` refuses audio over 1500 s; a margin so a recording cut *to* the cap
#: does not land a few frames over it.
_OPENAI_GPT_MAX_SECONDS = 1400
#: Voxtral takes three hours; the same margin.
_MISTRAL_MAX_SECONDS = 3 * 3600 - 120
_MISTRAL_MAX_BYTES = 900 * 1024 * 1024


def can_transcribe(provider: str | None) -> bool:
    return provider in TRANSCRIBING_PROVIDERS


def speech_base_url(config: ProviderConfig) -> str:
    """The provider's endpoint root — Mistral's is not OpenAI's, and a tenant rarely types it."""
    if config.base_url:
        return config.base_url.rstrip("/")
    return (MISTRAL_BASE_URL if config.provider == "mistral" else OPENAI_BASE_URL).rstrip("/")


@dataclass(frozen=True)
class SpeechLimits:
    """What one request to this provider/model may carry, and what it answers with."""

    max_bytes: int
    #: ``None`` — no duration cap the vendor states (whisper-1 is bounded by bytes alone).
    max_seconds: int | None
    #: Whether the answer labels speakers.
    diarize: bool
    #: Whether the answer carries segment timestamps at all.
    timestamps: bool


def speech_limits(config: ProviderConfig) -> SpeechLimits:
    """The vendor's stated ceilings for ``config`` — read by the meetings pipeline to decide
    whether a recording is one call or several. Conservative where a model is unknown: an
    OpenAI-compatible server is assumed to take what whisper takes and answer what whisper
    answers."""
    model = (config.model or "").lower()
    if config.provider == "mistral":
        return SpeechLimits(
            _MISTRAL_MAX_BYTES, _MISTRAL_MAX_SECONDS, diarize=True, timestamps=True
        )
    if "diarize" in model:
        return SpeechLimits(
            _OPENAI_MAX_BYTES, _OPENAI_GPT_MAX_SECONDS, diarize=True, timestamps=True
        )
    if model.startswith("gpt"):
        # ``gpt-4o-transcribe`` and its mini: text only, no segments, a duration cap.
        return SpeechLimits(
            _OPENAI_MAX_BYTES, _OPENAI_GPT_MAX_SECONDS, diarize=False, timestamps=False
        )
    return SpeechLimits(_OPENAI_MAX_BYTES, None, diarize=False, timestamps=True)


@dataclass(frozen=True)
class Segment:
    """One stretch of speech: when it started and ended (seconds into the clip), who spoke
    (the provider's own label, or ``None`` where it does not say) and what."""

    start: float
    end: float
    text: str
    speaker: str | None = None


@dataclass(frozen=True)
class Transcript:
    text: str
    #: What the provider reported, when it reported anything. Used for metering; 0 means the
    #: provider said nothing about duration, not that the clip was empty.
    seconds: int = 0
    #: Empty when the provider answers text only (``gpt-4o-transcribe``) or was not asked.
    segments: tuple[Segment, ...] = field(default_factory=tuple)


def _fields(
    config: ProviderConfig, *, language: str | None, diarize: bool, timestamps: bool
) -> dict[str, str]:
    """The multipart fields per provider/model — the one place the three dialects meet."""
    model = config.model or DEFAULT_SPEECH_MODELS.get(config.provider, DEFAULT_SPEECH_MODEL)
    data: dict[str, str] = {"model": model}
    if config.provider == "mistral":
        if diarize:
            data["diarize"] = "true"
        if timestamps:
            # Mistral's segment timestamps and its ``language`` field are mutually exclusive
            # (their documentation says so outright); the recogniser detects the language.
            data["timestamp_granularities"] = "segment"
        elif language:
            data["language"] = language
        return data
    if language:
        # The recogniser is far more accurate when told which language to expect, and the
        # user's own locale is the best available guess (§8) — never a hardcoded nl-NL.
        data["language"] = language
    lowered = model.lower()
    if diarize and "diarize" in lowered:
        data["response_format"] = "diarized_json"
        # Required by the diarize model for anything longer than a breath.
        data["chunking_strategy"] = "auto"
    elif timestamps and not lowered.startswith("gpt"):
        data["response_format"] = "verbose_json"
        data["timestamp_granularities[]"] = "segment"
    return data


def _speaker(raw: dict[str, Any]) -> str | None:
    for key in ("speaker", "speaker_id", "speaker_label"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def parse_transcription(payload: Any) -> Transcript:
    """The three dialects' answers into one shape. Exported for the tests, which feed it the
    vendors' documented bodies rather than a live key."""
    if not isinstance(payload, dict):
        raise AIProviderError("unexpected transcription response")
    text = payload.get("text")
    if not isinstance(text, str):
        raise AIProviderError("transcription response carried no text")
    segments: list[Segment] = []
    raw_segments = payload.get("segments")
    if isinstance(raw_segments, list):
        for raw in raw_segments:
            if not isinstance(raw, dict):
                continue
            words = raw.get("text")
            start, end = _number(raw.get("start")), _number(raw.get("end"))
            if not isinstance(words, str) or start is None or end is None:
                continue
            words = words.strip()
            if not words:
                continue
            segments.append(
                Segment(start=start, end=max(end, start), text=words, speaker=_speaker(raw))
            )
    seconds = 0
    duration = payload.get("duration")
    if isinstance(duration, int | float) and duration > 0:
        seconds = int(duration)
    else:
        usage = payload.get("usage")
        if isinstance(usage, dict):
            for key in ("seconds", "prompt_audio_seconds", "audio_seconds"):
                value = usage.get(key)
                if isinstance(value, int | float) and value > 0:
                    seconds = int(value)
                    break
    if seconds == 0 and segments:
        seconds = int(round(segments[-1].end))
    return Transcript(text=text.strip(), seconds=seconds, segments=tuple(segments))


async def transcribe(
    config: ProviderConfig,
    clip: AudioClip,
    *,
    language: str | None,
    diarize: bool = False,
    timestamps: bool = False,
) -> Transcript:
    """One transcription round trip. Raises :class:`AIProviderError` on any non-2xx.

    ``diarize`` / ``timestamps`` are *requests*: a provider that cannot label speakers answers
    without labels, and :func:`speech_limits` is what a caller reads to know in advance.
    """
    if not can_transcribe(config.provider):  # pragma: no cover - callers gate first
        raise AIProviderError(f"provider {config.provider!r} cannot transcribe")
    response = await client().post(
        f"{speech_base_url(config)}/audio/transcriptions",
        headers={"authorization": f"Bearer {config.api_key}"},
        data=_fields(config, language=language, diarize=diarize, timestamps=timestamps),
        files={"file": (clip.filename, clip.data, clip.content_type)},
    )
    await _raise_for_status(response)
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise AIProviderError("transcription response was not JSON") from exc
    return parse_transcription(payload)


__all__ = [
    "DEFAULT_SPEECH_MODEL",
    "DEFAULT_SPEECH_MODELS",
    "MISTRAL_BASE_URL",
    "TRANSCRIBING_PROVIDERS",
    "Segment",
    "SpeechLimits",
    "Transcript",
    "can_transcribe",
    "parse_transcription",
    "speech_base_url",
    "speech_limits",
    "transcribe",
]
