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

## Lifecycle

`recording → queued → transcribing → summarising → review → done`, or `failed` with the reason as
an i18n key on the row. The worker owns the two middle states and stamps `status_at`;
`meetings_reap_stale` fails a row held past `STALE_AFTER_MINUTES` (90 — a three-hour recording
through a slow provider is a legitimate forty minutes). `retry` re-queues a `failed` or `review`
row over the folded recording. The detail page polls `GET /meetings/{id}/status` — three columns —
while the row is in flight.

## Gates

- `meetings.meeting.read` (member), `.write` (member — record, review, confirm),
  `.delete` (admin). The router carries the module's licence write gate (`sku="meetings"`).
- Recording needs `meeting_assist` on (`AI_FEATURES`, its own key: a meeting is mostly *other
  people's* words sent whole to a model) and a speech provider that can transcribe
  (`SPEECH_FEATURES`); `POST /meetings` answers 409 otherwise and the screen draws no button.
- What confirm produces carries its own gates: the interaction write and the task create are
  refused by their own modules, and a refused task is *reported* on the result (`skipped`, with
  the field named) rather than failing the confirm — the minutes are the record, the tasks a
  convenience (§18's split).
- A portal login can never open a recording; reads follow the company horizon like every other
  row with `company_id`.

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
