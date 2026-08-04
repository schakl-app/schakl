# Dictated input

Speaking a time entry instead of typing it (#246). The browser records, the tenant's own speech
provider transcribes, and the transcript lands in the quick-add field on `/time` for the user to
read and correct before it is parsed.

## Why the transcription is server-side

The obvious cheaper option is the browser's own `SpeechRecognition` API: no backend, no
credential, no migration. It was rejected for one reason that matters on a self-hosted,
employee-facing product — **Chrome and Edge ship the audio to Google, Safari to Apple**. That is
a decision about where an agency's staff conversations go, and it is not one this product should
make on their behalf without asking. Recording locally and posting to the service the
organisation *already chose and configured* keeps the answer to "where does my voice go" the
same as the answer for every other AI feature here.

The cost of that choice is real and worth stating: dictation needs a speech provider configured,
and the default provider cannot be one.

## The speech credential is separate, and has to be

| Provider | Transcription |
|---|---|
| `anthropic` | **none** — Anthropic has no speech endpoint |
| `openai` | `POST {base}/audio/transcriptions` |
| `openai_compatible` | same shape, if the tenant's server implements it |

`anthropic` is the settings-page default, so "reuse the chat provider" resolves to nothing for
the typical tenant. `ai_settings.speech_provider` / `speech_base_url` / `speech_api_key_enc` /
`speech_model` let an org keep Claude for writing and point audio somewhere that can transcribe.

`NULL` means "reuse the chat provider" — correct for an OpenAI-configured org, and resolving to
"speech is off" for an Anthropic one. **Off means invisible**: `speech_available` is computed
server-side and the microphone is simply not drawn, rather than offered and then 409'd.

Anthropic is deliberately absent from the speech picker. Offering it would only let an admin
save a setting that can never work.

## The flow

```
MediaRecorder ──base64 in JSON──▶ /ai/[...path] proxy ──▶ POST /api/v1/ai/time/transcribe
                                                              │
                                          ctx.release_db() ───┤ multipart → speech provider
                                                              ▼
                                     transcript ──▶ quick-add field (editable)
                                                              │
                                                    user presses Quick add
                                                              ▼
                                                    POST /ai/time/parse  (unchanged)
```

**The transcript goes into the field, not straight into a parse.** Dutch proper nouns are the
weak link — "Jansen" comes back as "Janssen" often enough to matter — and `companies.find` is a
bare ILIKE with no fuzzy fallback, so a misheard client name yields a silent `null` id. That is
indistinguishable from the parse being broken, and it is only correctable while the words are
still visible. Seeing the transcript is the cheap fix; a speech-straight-to-form design removes
the only place to make it.

**Base64 in JSON, not multipart.** The web app reaches the API through one same-origin proxy
that forwards JSON (`routes/(app)/ai/[...path]/+server.ts`). A clip is tens of kilobytes, so the
33% encoding overhead costs less than a second transport for a single endpoint would.

## Limits, and why they are where they are

| Limit | Value | Where |
|---|---|---|
| Recording length | 60 s | `MAX_RECORD_MS`, browser side |
| Upload size | 8 MB decoded | `MAX_AUDIO_BYTES`, `core/ai/audio.py` |
| Monthly audio | tenant-set, in **seconds** | `monthly_audio_seconds_budget` |

`audio.py` follows `core/impex/parsing.py`'s stance exactly: **every cap is checked before the
work it bounds** (encoded length before decoding, so a 40 MB payload is rejected without being
decoded), the container is identified by **magic number** rather than a client-supplied name,
and over a limit is an **error, never a truncation**. Silently transcribing the first ten
seconds of a forty-second entry is the worst outcome available, because it looks like it worked.

The 60 s browser cap is not only cost: a forgotten microphone keeps the browser's recording
indicator lit, which reads as being spied on. For the same reason the recorder stops its tracks
on unmount.

## Permissions

The route declares `ai.use` — that is what makes the surface enumerable (§15,
`tests/test_rbac_deny_by_default.py`). The **service** additionally requires
`time.entry.write`, because the transcript exists to become a time entry and AI access alone
must not reach it.

The browser control mirrors both. `/time` is client-reachable, so a write control there
self-gates on the API's own key (`can(user, "time.entry.write")`) — **not** on `!isPortal`
(`docs/UX.md`, client-portal rule).

## Browser support

`MediaRecorder` + `getUserMedia` is available in every current browser, but `getUserMedia` is
**secure-context only**, so an `http://` host has no microphone regardless. Support is resolved
after mount via `recordingSupported()` and never inferred from a user agent; where it is
missing, the control is not rendered at all — the typed field beside it is the fallback, so
nothing becomes unreachable.

Recording state never rides colour alone (`docs/UX.md`): `aria-pressed` flips, the label
changes, and an elapsed counter appears.

## Trap for whoever adds security headers next

`apps/web/src/hooks.server.ts` sets a Content-Security-Policy but **no `Permissions-Policy`**,
and Traefik adds none. So nothing blocks the microphone today — but a `Permissions-Policy`
header added later without `microphone=(self)` will kill dictation silently: `getUserMedia`
rejects with `NotAllowedError`, which the UI reports as "microphone access was refused", and
the user will go looking in their browser settings for a permission they already granted.

## Where this goes next

`lib/core/voice/` is in `core/`, not in the time module, because the assistant panel and the
rich-text editor are the obvious next hosts. `VoiceButton` takes a `Recorder` and two callbacks;
nothing in it is time-specific.
