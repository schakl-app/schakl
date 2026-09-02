# Dictated input

Speaking a record instead of typing it. The browser records, the tenant's own speech provider
transcribes, and the transcript lands in a field the user can read and correct before it is
parsed. Two hosts: a **time entry** (#246, the quick-add field on `/time`) and a whole
**task** (#382, the sheet behind Taken → Inspreken).

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
"speech is off" for an Anthropic one.

**Off means invisible** (#126). `/meta/me`'s `ai_features` list carries `speech` alongside the
real feature keys, but only when `_speech_ready()` says the org has a provider that can
transcribe — so `aiEnabled(user, "speech")` gates the button and an Anthropic-configured org
is never shown a microphone that would 409 on the first click. It is a **capability, not a
toggle**: there is nothing to switch, and putting it in `AI_FEATURES` would have grown the
settings form, the per-feature model override and the web's `AIFeature` union for a non-choice.

**A capability still needs a host, and since #382 there are two of them.** `speech` is reported
when a provider can transcribe **and** at least one feature that dictates is enabled —
`SPEECH_FEATURES`, today `time_assist` and `task_assist`. Written as the single name it started
as, it coupled the *task* microphone to the *time* quick-add's toggle: an org that wanted
dictated tasks and no AI time entries got no microphone anywhere, with nothing on either screen
able to explain why.

The settings page reads the same answer as `AISettingsRead.speech_available`, resolved from the
same helper, so the admin screen and the mic can never disagree.

Anthropic is deliberately absent from the speech picker. Offering it would only let an admin
save a setting that can never work.

## The flow

```
MediaRecorder ──base64 in JSON──▶ /ai/[...path] proxy ──▶ POST /api/v1/ai/{time,tasks}/transcribe
                                                              │
                                          ctx.release_db() ───┤ multipart → speech provider
                                                              ▼
                                     transcript ──▶ an editable field
                                                              │
                                        /time: the user presses Quick add
                                        /tasks: the parse runs on its own
                                                              ▼
                                            POST /ai/time/parse · /ai/tasks/parse
```

**The transcript goes into a field, not straight into a parse** — and, for the assistant,
not straight to the model: a spoken instruction may end in a write (`docs/AI.md`, "The
assistant's reach"), and the words are only correctable while they are still visible.

**The transcript goes into a field, not straight into a parse.** Dutch proper nouns are the
weak link — "Jansen" comes back as "Janssen" often enough to matter — and `companies.find` is a
bare ILIKE with no fuzzy fallback, so a misheard client name yields a silent `null` id. That is
indistinguishable from the parse being broken, and it is only correctable while the words are
still visible. Seeing the transcript is the cheap fix; a speech-straight-to-form design removes
the only place to make it.

**And that is a rule about the textarea, not about the click** (#382). The task sheet parses
automatically, keeps the transcript above the draft, and offers *Opnieuw verwerken* after an
edit — the correction surface is what the rule asked for, and the second button was never the
point. Its sibling: **speaking again appends.** People dictate in breaths ("…en zet hem op
vrijdag"), so a second press adds to the first rather than replacing it, which `/time` already
did and the task sheet copies.

**Base64 in JSON, not multipart.** The web app reaches the API through one same-origin proxy
that forwards JSON (`routes/(app)/ai/[...path]/+server.ts`). A clip is tens of kilobytes, so the
33% encoding overhead costs less than a second transport for a single endpoint would.

## Limits, and why they are where they are

| Limit | Value | Where |
|---|---|---|
| Recording length | 2 min (a time entry) · **5 min** (a task, the assistant) | `MAX_RECORD_MS` / `MAX_TASK_RECORD_MS` / `MAX_CHAT_RECORD_MS`, browser side |
| Bitrate asked of the browser | 32 kbit/s Opus (≈ 1.2 MB for five minutes) | `AUDIO_BITS_PER_SECOND`, `recorder.svelte.ts` |
| Web server request body | **40 MB** (`BODY_SIZE_LIMIT`, adapter-node) | `apps/web/Dockerfile`; overridable per deployment |
| Upload size | 24 MB decoded (under OpenAI's 25 MB) | `MAX_AUDIO_BYTES`, `core/ai/audio.py` |
| Monthly audio | tenant-set, in **seconds** | `monthly_audio_seconds_budget` |

**The limit is shown while it counts, and reaching it is said.** `VoiceButton` prints the
elapsed time against the maximum (`0:42 / 5:00`), and a capture the cap ended — rather than
the speaker — sets `Recorder.stoppedAtLimit`, which every host turns into one sentence
(`voice.limit_reached`) above the transcript it still uses. A recording that stops on its own
and says nothing looks exactly like the feature being broken.

**Every hop in front of the API has to admit the clip, and the first release forgot one.** A
five-minute task dictation (1.1 MB of base64) answered `413 Content-length exceeds limit of
524288 bytes` — from **adapter-node**, whose default body cap is 512 kB, before the proxy route
ran and before the API saw a byte. Nothing on screen said "too long": the client read a
non-JSON 413 as "the AI service did not answer". Two fixes, and both generalise. The image
sets `BODY_SIZE_LIMIT` to what the API itself admits, so the two caps cannot disagree in the
browser's disfavour (a limit stated in one place and enforced in two is a limit enforced at the
lower one). And the client reads the **status** before the body (`transcribeFailureKey`): a
413 is *too long* whichever layer answered it and whether or not an envelope came with it.

`audio.py` follows `core/impex/parsing.py`'s stance exactly: **every cap is checked before the
work it bounds** (encoded length before decoding, so a 40 MB payload is rejected without being
decoded), the container is identified by **magic number** rather than a client-supplied name,
and over a limit is an **error, never a truncation**. Silently transcribing the first ten
seconds of a forty-second entry is the worst outcome available, because it looks like it worked.

The browser cap is not only cost: a forgotten microphone keeps the browser's recording
indicator lit, which reads as being spied on. For the same reason the recorder stops its tracks
on unmount. The length is a **constructor argument** rather than a module constant, because how
long is right depends on what is being dictated — a time entry is one clause, a task carries its
steps — and a host that needs longer must not be able to get it by editing a number every other
host shares.

**Every way out of a dictating surface has to release the microphone, not only the one you
wrote a handler for.** Found in a browser, not in review: `SlideOver` owns three of its four
exits (the ✕, the backdrop and Escape) and closes by writing `open` itself, so the task sheet's
own Annuleren covered exactly one of them — dismissing it mid-sentence left the capture running,
the counter climbing behind a closed panel and the recording indicator lit. The fix watches
`open`, because that is the only thing all four exits agree on.

## Permissions

The route declares `ai.use` — that is what makes the surface enumerable (§15,
`tests/test_rbac_deny_by_default.py`). The **service** additionally requires the permission the
transcript exists to exercise: `time.entry.write` for `/ai/time/transcribe`, `tasks.task.create`
for `/ai/tasks/transcribe`. AI access alone must not reach a microphone that bills the tenant's
audio budget. `/ai/assistant/transcribe` asks for none — its transcript is a chat message the
user still has to send, and every tool that message can reach carries its own gate.

**The speech gate reads the host's own toggle.** `speech_config(feature)` used to test
`time_assist` for every caller, so a tenant with the time quick-add off and dictated tasks on
was drawn a microphone (`SPEECH_FEATURES` said so) that answered 409 on the first press. The
capability and the gate now read the same switch, per host; `test_ai_assistant_reach.py` pins
it with the assistant on and the time quick-add off.

The browser control mirrors both. `/time` and `/tasks` are client-reachable, so a write control
there self-gates on the API's own key — **not** on `!isPortal` (`docs/UX.md`, client-portal
rule).

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

## Dictating a whole task (#382)

`POST /ai/tasks/transcribe` then `POST /ai/tasks/parse` → a draft the speaker reviews in a
`SlideOver` and confirms; `POST /api/v1/tasks` writes it in one call, carrying the checklist,
the links and the labels. `app/core/ai/taskdraft.py` holds the prompt, the tool schema and the
grounding; the module docstring holds the argument.

The decision worth repeating here: **the vocabulary is the whole task form, and that follows
from who spoke and who is watching.** #327 (email → task) narrows what a model may write to six
fields because its input is written by an outsider and applied by an ARQ worker with nobody in
front of a screen. Both halves are inverted here — the words are a colleague's own voice on a
session holding `tasks.task.create`, and nothing is written until they press a button beside
every field it filled in. Copying the narrow schema would have kept the shape and dropped the
reason, and the only effect would be the speaker retyping the half it refused to carry.

What does *not* change is grounding, and here it is **per type**: `assignee_user_id` and
`label_ids` are checked against their own evidence sets rather than the single pool the time
parse uses. A project id offered as a company fails the write anyway; another entity's id in
`assignee_user_id` is a real user id from the same space. A misheard name comes back as *no
client selected* — one click to fix — never as somebody else's client, which nobody notices.

Two smaller ones, both from running it rather than reading it. **A parse that yields nothing
must not throw the words away**: the review still opens, with the transcript as the title and a
line saying which of the two happened. And **a field the model filled is marked as such** (a ✦
beside the label), so "schakl picked this client" and "I picked this client" are not the
same-looking cell.

## Where this goes next

`lib/core/voice/` is in `core/`, not in the time module — which is what let #382 reuse all of
it. `VoiceButton` takes a `Recorder` and two callbacks; nothing in it is time- or task-specific.

The **assistant panel** is the third host: a microphone beside its composer, five minutes,
appending, through `/ai/assistant/transcribe`. It is the transcript-into-a-field shape with no
parse at all — the model *is* the parse — which is what made it a morning's work and is the
argument for the ordering below.

The ordering worth keeping, by how much typing it removes: **contact moments** (a client call
logged in the ten seconds after it ends — same parse shape, different schema, and the highest
value left), then **dictating into a task that already exists** and a **task comment**, then the
**rich-text editor** wherever it appears. The line: a transcript-into-a-field is nearly free
and should be the default offer; a *parse* is only worth building where the record has more
than three fields.
