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
through a slow provider is a legitimate forty minutes) and ends a `recording` row nothing has
posted a piece to for `RECORDING_STALE_AFTER_MINUTES` (20; see below). `retry` re-queues a
`failed` or `review` row over the folded recording. The detail page polls `GET /meetings/{id}/status` — three columns —
while the row is in flight.

## What a redeploy does to a recording, and what a dead tab leaves behind

A Swarm redeploy (`docs/DEPLOY.md`) is not an outage for the recorder, and the module is built so
that it stays that way: the capture is `MediaRecorder` in a tab that is already loaded, nothing
polls for a new app version, and the service-worker registration never reloads a page. What a
redeploy *can* do is refuse a piece for a while — a request that meets an API task being retired,
the edge stack restarting, or simply a phone stepping out of wifi at the same moment — and the
first recorder answered that with three retries over twelve seconds, then a red line and a button.
The person who would press the button is in the meeting. Four rules now hold.

- **A row exists only for a capture that is already running.** The record screen used to create
  the meeting and *then* ask for the microphone, so every way a capture can fail to begin — a
  permission prompt nobody answers, a phone that freezes the tab while the prompt is up, a
  `MediaRecorder` the browser declines to build — left a row stamped `recording` behind it with
  nothing to record into. `MeetingRecorder.arm()` acquires the microphone and builds the
  recorder, `begin()` starts it, and the row is created between the two; a create that fails
  aborts the armed capture so the phone stops showing a microphone nobody has a meeting for.
- **The first piece is asked for after seconds** (`FIRST_CHUNK_MS`, five), with `requestData()`
  rather than a shorter timeslice. Everything that kills a recording kills it at the start, and
  until a piece lands there is nothing on the server at all — not a byte to transcribe, and no
  evidence the capture was ever really running. With it, a recording that dies in its first
  minute is a short recording rather than no recording. It also makes *"opgeslagen tot"* a
  measured number (`savedSeconds`, the elapsed second the last landed piece was cut at) instead
  of `uploaded × 60`, which claimed a whole minute was safe five seconds in.
- **The screen wake lock is re-taken on every return to visibility.** The browser releases it
  the moment the page hides and hands it back to nobody, so asking once at the start bought
  exactly one screen-off; after that a phone slept on its own schedule, froze the tab, and
  stopped the recording with nothing saying so. And a capture that did not survive being away is
  *ended* rather than left to a timer that goes on ticking: a `MediaRecorder` the browser tore
  down, or a microphone the OS took back for an incoming call, stops the recording, hands over
  every piece that landed, and says why (`meetings.record.capture_lost`).
- **A piece is retried for minutes, not seconds** (`upload.ts`): a capped backoff (1 s → 30 s)
  until `UPLOAD_RETRY_BUDGET_MS` (ten minutes) has been spent waiting, held in memory, in order.
  The screen says *"Verbinding herstellen… opgeslagen tot 12:03"* in amber while it retries — the
  recording goes on and nothing is lost yet — and turns red with *Opnieuw opslaan* only once the
  budget is gone. A refusal the API means (a 4xx) still comes back at once. The API replaces a
  piece it already holds under the same `seq`, so a retry after a lost response cannot double a
  minute. The API task gets `stop_grace_period: 30s` so the upload it holds at SIGTERM finishes.
- **Leaving is asked about, twice.** While a capture runs, `beforeunload` puts the browser's own
  prompt on a reload, a closed tab or a typed URL, and `beforeNavigate` asks in our words before a
  nav link or the back button — because the page's unmount aborts the capture *and deletes the
  row*, and a mis-click must not be how a meeting ends.
- **A tab that died anyway leaves a row the page can finish.** The pieces it uploaded are stored;
  what is missing is the stop. Every piece bumps `status_at` and a recorder posts one a minute,
  so the detail page polls a `recording` row and, once nothing has landed for fifteen minutes,
  says so and offers *Verwerk wat is opgeslagen* — `POST /finish` without a duration, the
  transcription's own count filling it in — or the delete.
- **And the server ends it whether or not anybody opens the page.** The bullet above was the
  whole answer once, and it was an answer only for somebody who thinks to look. The first
  three-hour meeting recorded on a phone made the gap plain: the tab was frozen by a screen lock
  minutes in, no piece ever landed, and at half past ten the row still said *"de opname loopt nog
  op een ander scherm"* with Verwijderen as the only control on the page. So
  `meetings_reap_stale` reaps `recording` too (`RECORDING_STALE_AFTER_MINUTES`, twenty), and what
  it does depends on what arrived: **pieces were stored**, so the meeting is queued and
  transcribed from them — the recorder's own *Verwerk wat is opgeslagen*, taken without a person
  pressing it; **nothing arrived at all**, so there is no recording, only a row claiming to be
  one, and it is failed with `meetings.error.abandoned`. Failed rather than deleted: the title,
  the client and the roster the person typed *did* reach us and are the half worth keeping. The
  colleague who pressed record is **told** (`meeting.lost`, which mails by its own default like
  `meeting.ready`): silence is what made this expensive — somebody walked out of a three-hour
  meeting believing it had been recorded and found out hours later, by opening the row.
- **The three timers are ordered, and the order is the design.** A piece gives up at ten minutes
  (`UPLOAD_RETRY_BUDGET_MS`), the screen offers to finish without the recorder at fifteen
  (`STALLED_AFTER_MS`), the server finishes it at twenty. Being early here is expensive and being
  late is only slow: offer at two minutes and somebody processes a meeting that is still being
  recorded, which 409s every remaining piece and loses the rest of it to save the start.
  `tests/unit/meeting-recorder-order.test.ts` pins the ordering across the three files, because
  each number reads as correct on its own.
- **The worker resumes a run its own restart cut short.** The worker rolls stop-first; arq
  cancels the running `meetings_process` and queues it again, and the second run arrives to a row
  still stamped `transcribing` or `summarising`. `run_pipeline` used to stand down on anything but
  `queued`, which handed a ten-second restart to the ninety-minute reaper and then to a person
  pressing retry. A row in a worker state is resumed now — from the minutes where the transcript
  is already committed (the row was stamped `summarising` in the same commit that stored it), so a
  second transcription is never bought for the same audio. A row on `review`, `done` or `failed`
  is still nobody's to resume.

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

## The minutes as a document, the words as a file

`GET /meetings/{id}/pdf?sections=summary,decisions,…` prints the minutes; `GET …/preview` is the
same HTML the PDF comes out of (`render/`), which is what the export dialog's "voorbeeld" opens
and what the settings screen's live preview draws — one artefact, so a preview and a download
cannot disagree (invoicing's rule, inherited with the shared `app/core/documents` engine). The
design is the reporting document's register: the tenant's logo and the client's, the accent
contrast-corrected against paper, a heading strip per chapter that bleeds to the paper's edge
because a strip one line tall can never be cut by a page break, participants grouped
**under the agency's name and the client's** (and *Overig*) — which side somebody is on is the
first thing a reader of the attendance wants — with a picture where one is known and initials
otherwise, decisions numbered with their evidence in a quieter voice underneath, action items
under the person who owns them by side, the transcript as an appendix on its own page whose
speakers are **names**, never the provider's `S2` (an unpaired label prints as *Spreker 2*).
Nobody is printed as "recorded by": who pressed the button is the agency's business, not the
reader's.

**A picture reaches paper as bytes the API read itself** (`app/core/avatars.py`): a personal
upload through the org-scoped image loader, and otherwise the identity provider's picture —
which for a Google Workspace sign-in is every colleague's — fetched only from a closed list of
IdP picture hosts, over HTTPS, without redirects, from public addresses, capped and cached per
process. A URL off the list is never requested: the column is filled from a login's claims, and a
render must not become a way to make the API call an arbitrary address.

**Every text field of the minutes is markdown, and may carry an image.** The summary, the topics,
each decision, each action item's toelichting and each open question are the shared
`RichTextEditor` — bold, lists, links, the writing assist (*Verbeteren, Inkorten, …*), and an
image pasted, dropped or picked, stored against the meeting as body content (`inline=true`, the
task description's shape) and written as `![alt](file:<id>)`. The document draws such an image
only when the file belongs **to this meeting** (`render._body_images` → `markdown_to_html(…,
images=…)`); a pasted id of another record's file simply does not print. The **topics are edited
as one field** — a heading per topic, the words under it (`topics.ts` round-trips it) — because a
card per topic with a heading box and a text box is a form for something that is really a
document; the stored shape stays a list, since the document, the contact moment and the AI box
address topics one by one. Decisions, action items and questions stay separate rows: they carry
evidence, an owner, a task and a number, which one markdown field cannot. The minutes prompt asks
the model to use the formatting that makes minutes scannable (bold the fact, lists for parallel
points, a small table for a comparison) and nothing for its own sake.

**The chapters are ticked per download** (`DOCUMENT_SECTIONS`: participants, summary, topics,
decisions, action_items, open_questions, evidence, transcript). The org's defaults live in
Instellingen → Vergaderingen (`meeting_settings.document_sections`), the row's own
`document_sections` narrows them to what this meeting has anything for, and the **transcript is
off unless asked for** — it is the longest thing on the record and the one a reader of the
minutes least often wants on paper. The *unverified* mark prints whatever was ticked: a claim the
transcript does not support is a warning the reader needs, not a chapter.

**The transcript is its own route** (`GET /meetings/{id}/transcript`, `transcript.py`): JSON by
default — every line with the seconds it was said at and the speaker's *name* where the roster
names one, plus the flat text, which is what an MCP client reads — and `format=txt|md|srt|vtt`
for a file. One route for the export button and the generated tool, because two routes are two
answers that drift. Three curated tools ride the `mcp_tools` seam beside it (`mcp.py`):
`meetings.find`, `meetings.transcript`, `meetings.minutes`, each behind `meetings.meeting.read`
in both places the rule has to hold (the offer is filtered *and* the service refuses).

**Instellingen → Vergaderingen** (`settings.py`, `meeting_settings`, `meetings.settings.manage`)
holds three things that are the org's and nobody's else: whether the recorder asks for the
consent statement (`consent_required`, on by default — off drops the checkbox *and* the refusal
together, never one without the other, for an agency whose own procedure covers it); the
document (design, accent, header image, closing line, default chapters, avatars on or off, and a
tenant's own Jinja + CSS in the shared sandbox, validated at save time so a template that cannot
render is refused under the editor rather than at somebody's first download); and the agency's
own **house rules for the minutes** (`ai_instructions`), which reach the model inside the system
prompt's rules block as a style — never as licence to add what the transcript does not say. The
recorder reads only `GET /meetings/policy` (whoever may record), never the settings.

**A recording plays on a phone because the file server answers byte ranges.** iOS Safari probes
a media URL with `Range: bytes=0-1` and refuses to play from a server that answers `200` and the
whole file, which is what "the recording will not play on my phone" was; `GET /files/{id}`
answers `206` + `Content-Range` (and `Accept-Ranges` on every response) for any stored file now.
The other half is the container: Safari plays no WebM, and every Chrome-made recording is one, so
the page asks the element `canPlayType` first and draws a sentence with a download link instead
of a dead player (`audio_content_type` on the row).

**The AI box** (`assist.py`, `POST /meetings/{id}/ai/revise`, the task revise's shape) changes a
meeting in a colleague's own words, applied *as them* through the service an ordinary edit goes
through: the title, kind, date, client and project; the roster and its speaker labels; and —
while the minutes are under review — every part of the draft, each addressed by the index the
document numbered it with. Every id is grounded in what the model was shown, a due date is
bounded, and a confirmed meeting's minutes are not touched whatever the answer says. The
reviewer's unsaved draft is saved before the model reads it, so the box changes what the reader
sees.

**The recorder is told when the minutes are ready** (`meeting.ready`, emitted by the worker the
moment the draft lands on `review`, deduped per run so a redraft is heard too). Immediate in the
app, and — the one event that mails by its own default (`EMAIL_DEFAULT_ON_EVENTS`) — by mail,
because the person waiting for it recorded from a phone and walked out of the room; a person or
an org switches it off in the matrix like any other row.

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
