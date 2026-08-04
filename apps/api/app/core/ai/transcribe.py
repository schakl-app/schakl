"""Speech-to-text against the tenant's own provider (#246).

Deliberately a **third top-level provider function** rather than a branch inside
``stream_chat``: the request is multipart and the response is one JSON object, so it shares
neither the SSE reader nor the event normalisation that the whole of ``providers.py`` is built
around. Forcing it through there would mean special-casing every layer for a call that has none
of the same shape.

Only OpenAI-shaped providers are reachable, because that is the only transcription API in play:
``POST {base}/audio/transcriptions``, multipart, Bearer. **Anthropic has no speech endpoint**,
which is exactly why the speech credential is configured separately (``AISettings.speech_*``) —
an org can keep Claude for writing and still dictate.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ai.audio import AudioClip
from app.core.ai.providers import (
    OPENAI_BASE_URL,
    AIProviderError,
    ProviderConfig,
    _raise_for_status,
    client,
)

#: Providers whose HTTP API we know how to ask for a transcript.
TRANSCRIBING_PROVIDERS: frozenset[str] = frozenset({"openai", "openai_compatible"})
#: A sensible starting point; the tenant may type anything their server offers.
DEFAULT_SPEECH_MODEL = "whisper-1"


def can_transcribe(provider: str | None) -> bool:
    return provider in TRANSCRIBING_PROVIDERS


@dataclass(frozen=True)
class Transcript:
    text: str
    #: What the provider reported, when it reported anything. Used for metering; 0 means the
    #: provider said nothing about duration, not that the clip was empty.
    seconds: int = 0


async def transcribe(
    config: ProviderConfig, clip: AudioClip, *, language: str | None
) -> Transcript:
    """One transcription round trip. Raises :class:`AIProviderError` on any non-2xx."""
    if not can_transcribe(config.provider):  # pragma: no cover - callers gate first
        raise AIProviderError(f"provider {config.provider!r} cannot transcribe")
    base = (config.base_url or OPENAI_BASE_URL).rstrip("/")
    data: dict[str, str] = {"model": config.model or DEFAULT_SPEECH_MODEL}
    if language:
        # The recogniser is far more accurate when told which language to expect, and the
        # user's own locale is the best available guess (§8) — never a hardcoded nl-NL.
        data["language"] = language
    response = await client().post(
        f"{base}/audio/transcriptions",
        headers={"authorization": f"Bearer {config.api_key}"},
        data=data,
        files={"file": (clip.filename, clip.data, clip.content_type)},
    )
    await _raise_for_status(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise AIProviderError("transcription response was not JSON") from exc
    if not isinstance(payload, dict):
        raise AIProviderError("unexpected transcription response")
    text = payload.get("text")
    if not isinstance(text, str):
        raise AIProviderError("transcription response carried no text")
    duration = payload.get("duration")
    seconds = int(duration) if isinstance(duration, int | float) and duration > 0 else 0
    return Transcript(text=text.strip(), seconds=seconds)


__all__ = [
    "DEFAULT_SPEECH_MODEL",
    "TRANSCRIBING_PROVIDERS",
    "Transcript",
    "can_transcribe",
    "transcribe",
]
