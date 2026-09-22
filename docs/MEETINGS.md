# Meetings

A recorded meeting into a transcript, minutes, decisions and action items — the action items
as tasks, the minutes as a contact moment on the client. `apps/api/app/modules/meetings/`,
`apps/web/src/lib/modules/meetings/`, screens under `routes/(app)/meetings/`. Phase 1: the
microphone, a browser tab's call, an uploaded file. Google Meet's own recordings are phase 2
(see the end).

## What it is, in one sentence per layer

- **The browser records and uploads while it records.** `MeetingRecorder` asks `MediaRecorder`
  for a blob a minute (`CHUNK_MS`) and posts each as `POST /meetings/{id}/chunks` — base64 in
  JSON, the dictation's transport, so an MCP client can post a recording the same way. Only the
  first piece carries the container header; the rest are continuations, and byte-concatenating
  them in order *is* the file. A crashed tab loses a minute, not the meeting. An uploaded file
  takes the same route cut into 4 MiB pieces.
- **The worker folds, transcribes and drafts** (`jobs.meetings_process`): the pieces become one
  `files` row (`entity_type = "meeting"`, `audio_file_id`), the recording goes to the tenant's
  speech provider in as many requests as that provider takes (`pipeline.py`), the transcript
  lands on the row as JSONB segments, and one forced tool call (`minutes.py`) drafts the minutes.
- **A person confirms** (`POST /meetings/{id}/confirm`): the reviewer's edited draft becomes an
  interaction of kind `physical_meeting` / `online_meeting` through the interactions module's
  own service and a task per ticked action item through the tasks module's — as the reviewer,
  so every rule a hand-made record meets applies and the trail names the person.

## The three decisions worth knowing

**Which speech provider, and why it decides the shape.** A provider labels speakers *per
request* and takes a bounded amount of audio per request (`core/ai/transcribe.speech_limits`):

| Provider / model | Per request | Speakers | Timestamps |
|---|---|---|---|
| Mistral `voxtral-mini-latest` | 3 h, ~1 GB | yes | yes |
| OpenAI `gpt-4o-transcribe-diarize` | 25 min, 25 MB | yes | yes |
| OpenAI `gpt-4o-transcribe` | 25 min, 25 MB | no | no |
| OpenAI `whisper-1` / compatible | 25 MB | no | yes |

A recording over the cap is cut into parts with ffmpeg (a copy cut on a frame boundary, no
re-encode; the API image ships it, a dev box without it refuses exactly those recordings with
`meetings.error.needs_split`). Every part is a fresh request, so "S1" in part two is not "S1" in
part one: labels are numbered on through the parts (S1, S2 · S3, S4) and never merged by
guesswork, and the review screen says the recording was transcribed in *n* parts beside the
speaker names. That is the whole argument for Voxtral: a two-hour meeting is one request, one
set of labels, in Dutch. The settings screen offers it as a speech provider
(`SpeechProvider = "mistral"`); its chat API stays reachable as `openai_compatible`.

**Every claim quotes its evidence, and the quote is checked.** The minutes schema asks for the
transcript's own words under every decision and action item, with the second it was said at.
`minutes.quote_found` looks the quote up after normalising case, accents, punctuation and
whitespace, and an item whose quote is not there is **kept and marked** `verified: false` — a
reviewer judges a paraphrase in a second, a dropped item is a decision nobody sees. What the check
buys is precise: a model cannot *invent* an agreement without the review screen saying so.
Assignees are grounded in the staff shortlist (a misheard name is *nobody*, never a colleague who
was not there), and a due date is bounded to the window every model-read date gets. The client's
own promises land with an `owner_label` and `create_task: false`: they are minuted, not put on our
board, unless the reviewer ticks them to chase.

**Recording is a statement before it is a capture.** `POST /meetings` refuses without
`participants_informed: true`. Recording a conversation you take part in is legal in the
Netherlands; not telling the others is not (AVG art. 13; Sr 139a/b for a secret recording), and
the screen states what the person is asked to say — why, who reads it, how long the audio is kept.
The audio has a retention: `meetings_sweep_audio` drops the recording of a confirmed meeting after
`AUDIO_RETENTION_DAYS` (30); the transcript and the minutes stay. A reviewer can drop it earlier.

## Who was there, and who took what on

A meeting's roster is `meetings.participants`: a list of people, each a colleague (`user_id`), a
contact of the client (`contact_id`) or somebody known by name alone, and each optionally
holding the provider's speaker label they turned out to be (`speaker: "S2"`). The recorder asks
for it **before** the recording — the person knows who is at the table — and adds the recorder
themselves; the review screen finishes it, pairing labels with people beside the transcript's
lines, with the client's contacts offered from the client's own roster (and the ordinary
inline-create behind the ＋). The first shape, `speakers = {"S2": "Jan"}`, is still read for the
rows written under it and never written again: it could say what a label was and not who was in
the room, so nothing could be grounded in it.

Three things follow from the roster being *people*:

- **The model is told who spoke.** The prompt carries a `PARTICIPANTS` block (label, name,
  kind, id), and the rule that an action item's owner is the speaker who said they would do it.
  `owner_contact_id` joins `assignee_user_id` on an action item and is grounded exactly as
  strictly — only a contact the block named; a contact and a colleague on one item resolves to
  the contact, because a client's promise is never a colleague's task.
- **The minutes are written by side and then by person**: *Voor ons* under each colleague,
  *Voor de klant* under each contact, *Overig* for the rest — the review screen groups the same
  way, so either side reads its own list. Confirm puts the client's contacts on the contact
  moment as its roster (`contact_ids`), and a ticked item owned by a contact becomes a task
  **assigned to that contact** (`assignee_contact_id`, the "waiting on the client" shape).
- **Naming the speakers after the draft is the common case**, so `POST /meetings/{id}/redraft`
  writes the minutes again over the transcript already on the row (`meetings_process` with
  `stage="minutes"`): no new transcription, no audio cost, the people known this time. The
  screen's button saves the roster first, so the redraft reads what the screen shows.

**A speech model that does not label speakers is said by name, before and after.** A transcript
with words and no labels looks exactly like a recording in which nobody could be told apart, so
`enabled_features` reports `speech_diarize` beside `speech` (read off `speech_limits` for the
configured model), the recorder shows an amber line above the record button when it is absent,
the review screen names the model (`transcript.diarized`), and Instellingen → AI says under the
model field which models label speakers. Found on the first live meeting: the instance's speech
model was `gpt-transcribe`, which answers text only, and nothing on any screen had said so.

## Lifecycle

`recording → queued → transcribing → summarising → review → done`, or `failed` with the reason as
an i18n key on the row. The worker owns the two middle states and stamps `status_at`;
`meetings_reap_stale` fails a row held past `STALE_AFTER_MINUTES` (90 — a three-hour recording
through a slow provider is a legitimate forty minutes). `retry` re-queues a `failed` or `review`
row over the folded recording. The detail page polls `GET /meetings/{id}/status` — three columns —
while the row is in flight.

## Gates

- `meetings.meeting.read` (member), `.write` (member — record, review, name the
  participants, redraft, confirm),
  `.delete` (admin). The router carries the module's licence write gate (`sku="meetings"`).
- Recording needs `meeting_assist` on (`AI_FEATURES`, its own key: a meeting is mostly *other
  people's* words sent whole to a model) and a speech provider that can transcribe
  (`SPEECH_FEATURES`); `POST /meetings` answers 409 otherwise and the screen draws no button.
- What confirm produces carries its own gates: the interaction write and the task create are
  refused by their own modules, and a refused task is *reported* on the result (`skipped`, with
  the field named) rather than failing the confirm — the minutes are the record, the tasks a
  convenience (§18's split).
- **Who may see a meeting** is answered in three layers, and each is stated once. The
  **permission** is `meetings.meeting.read` on every route, in the service and on the hub
  panel. The **company horizon** rides the tenant-scoped repository: a member restricted to a
  company group sees the meetings on those clients (plus meetings attached to no client, the
  platform-wide rule for a nullable `company_id`), and cannot record onto or move a meeting to a
  client outside it. The **portal** answer is `Meeting.__portal_horizon_clause__`, and it is
  *nothing*: a transcript is a verbatim record of what the agency's people said and a draft is
  prose nobody has confirmed, so a client — even one a tenant grants the read key to — gets an
  empty list, a 404 on every id, and no company panel; the confirmed contact moment is what they
  are owed, and `interactions` serves it under its own rules. `POST /meetings` refuses a portal
  login outright on top of that.
- **The recording reads exactly when the meeting does.** `meeting` is a record-gated file host
  (`RECORD_GATED_ENTITY_TYPES`, `docs/STORAGE.md`): `GET /files/{id}` for the folded audio or a
  chunk, and `GET /files?entity_type=meeting`, ask the meeting's own read key (the one its trail
  registered, `read_permission_for`) and then its horizon and portal clause through
  `entity_visible`. Before this the bytes were tenant-scoped only — any signed-in member with the
  file id, a client included, could pull a recording — which for the most sensitive blob the
  module holds was the wrong default.

## Costs

A one-hour meeting: about €0.20 of transcription on Voxtral (~€0.35 on `gpt-4o-transcribe`),
metered in `ai_usage.audio_seconds` under `meeting_assist`, plus ~15k tokens for the minutes,
metered as tokens. Both budgets apply; a worker never overrides them.

## Phase 2: Google Meet

Meet's own transcripts do not support Dutch (EN/FR/DE/IT/JA/KO/PT/ES as of September 2026), so
the honest route is the Meet REST API's **recordings** (`conferenceRecords.recordings`, an MP4 in
the organiser's Drive; Business Standard or higher, somebody pressed Record) pulled through the
existing `google` integration and pushed through this pipeline — which needs ffmpeg to take the
audio out of the MP4, and is why the image ships it now. Until then, a Meet in the browser is the
**tab** source, which also covers Teams and Zoom.
