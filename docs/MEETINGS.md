# Meetings

A recorded meeting into a transcript, minutes, decisions and action items — the minutes as a
contact moment on the client from the moment they land, editable for ever; an action item as a
task the moment somebody makes one of it. `apps/api/app/modules/meetings/`,
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
- **The minutes are filed, and stay editable.** The moment the draft lands the worker writes it
  onto the client's timeline as an interaction of kind `physical_meeting` / `online_meeting`,
  through the interactions module's own service **as the colleague who recorded it**
  (`member_context`, the e-mail intake's shape; `jobs._file`), and every later edit — the
  minutes, the title, the filing, the roster — rewrites that moment
  (`MeetingService.sync_interaction`). A task is made of one action item at a time from the page
  (`POST …/action-items/task`, checked in the task sheet), and the hours are booked from their
  own button (`POST …/time`). There is **no confirm step**: there was one, and it froze the
  minutes the moment somebody spotted a typo in them, held the contact moment back until a button
  nobody asked for was pressed, and made tasks by checkbox in bulk. Every write still goes through
  the owning module's service as the person acting, so the trail names them.

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
part one. The first shape numbered the labels on through the parts (S1, S2 · S3, S4) and left the
reviewer to pair four labels with two people — which, on a twenty-three-minute meeting with two
speakers, was not feasible, and that meeting should never have been cut at all: the margin under
the vendor's 1500 s was taken twice (1400 × 0.97 = 1358 s), so a meeting well inside what the
model takes was split. One margin now, in one place (`_PART_MARGIN`). Where a recording really
is longer than a request, **the parts overlap** (`OVERLAP_SECONDS`, 45): every part but the first
starts before the previous one ended, both transcribe the same stretch, and a label in the new
part is matched to the label in the old one that spoke during the same seconds
(`pipeline.align_labels`) — by time, never by guessing at voices, greedily best pair first, and
only where the shared speaking time is at least two seconds and at least half of the new label's
time in the window. A label the overlap cannot pair keeps a fresh number, so the failure direction
is the old one (a speaker split in two, said on the screen with `transcript_aligned`), never two
people merged into one; the duplicated stretch is dropped from the new part (a segment straddling
the cut is kept once, by whichever part holds more of it) and the flat text is rebuilt from the
rows. A provider that answers no timestamps gets edge-to-edge parts: there is nothing to align on.
That is still the argument for Voxtral: an alignment is an inference and one request is a fact —
a two-hour meeting is one request, one set of labels, in Dutch. The settings screen offers it as
a speech provider (`SpeechProvider = "mistral"`); its chat API stays reachable as
`openai_compatible`.

**Every claim quotes its evidence, and the quote is checked.** The minutes schema asks for the
transcript's own words under every decision and action item, with the second it was said at.
`minutes.quote_found` looks the quote up after normalising case, accents, punctuation and
whitespace, and an item whose quote is not there is **kept and marked** `verified: false` — a
reviewer judges a paraphrase in a second, a dropped item is a decision nobody sees. What the check
buys is precise: a model cannot *invent* an agreement without the review screen saying so.
Assignees are grounded in the staff shortlist (a misheard name is *nobody*, never a colleague who
was not there), and a due date is bounded to the window every model-read date gets. The client's
own promises land with an `owner_label` (or a contact on the roster): they are minuted, not put on
our board, unless somebody makes a task of them — which assigns it to the contact.

**Recording is a statement before it is a capture.** `POST /meetings` refuses without
`participants_informed: true`. Recording a conversation you take part in is legal in the
Netherlands; not telling the others is not (AVG art. 13; Sr 139a/b for a secret recording), and
the screen states what the person is asked to say — why, who reads it, how long the audio is kept.
The audio has a retention: `meetings_sweep_audio` drops the recording `AUDIO_RETENTION_DAYS` (30)
after the minutes landed; the transcript and the minutes stay. A colleague can drop it earlier.

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
  way, so either side reads its own list. The client's contacts on the roster are the contact
  moment's roster (`contact_ids`, kept in step on every roster save), and a task made of an item
  owned by a contact is **assigned to that contact** (`assignee_contact_id`, the "waiting on the
  client" shape).
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

`recording → queued → transcribing → summarising → ready`, or `failed` with the reason as an
i18n key on the row. `ready` is the only state after the worker: the minutes are in, on the
client's timeline, and editable for as long as the meeting exists (`review` and `done` collapsed
into it in `b7d4f2c9a1e6`). The worker owns the two middle states and stamps `status_at`;
`meetings_reap_stale` fails a row held past `STALE_AFTER_MINUTES` (90 — a three-hour recording
through a slow provider is a legitimate forty minutes) and ends a `recording` row nothing has
posted a piece to for `RECORDING_STALE_AFTER_MINUTES` (20; see below). `retry` re-queues a
`failed` or `ready` row over the folded recording. The detail page polls
`GET /meetings/{id}/status` — three columns — while the row is in flight.

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
  second transcription is never bought for the same audio. A row on `ready` or `failed` is
  still nobody's to resume.

## Gates

- `meetings.meeting.read` (member), `.write` (member — record, edit the minutes, name the
  participants, redraft, file, book the hours), `.delete` (admin). The router carries the
  module's licence write gate (`sku="meetings"`).
- Recording needs `meeting_assist` on (`AI_FEATURES`, its own key: a meeting is mostly *other
  people's* words sent whole to a model) and a speech provider that can transcribe
  (`SPEECH_FEATURES`); `POST /meetings` answers 409 otherwise and the screen draws no button.
- What the meeting writes elsewhere carries that module's gates. The **contact moment** is the
  interactions module's write: a refusal there (the kind deactivated, no `interactions.
  interaction.write` for the recorder, the module off) is logged and swallowed by
  `sync_interaction` — the meeting is the record and the moment its mirror, so an edit is never
  lost to save the mirror — and the page then offers *Als contactmoment vastleggen*
  (`POST …/interaction`, the strict form) whose refusal carries the reason. The **task** is the
  tasks module's own 422s, on the sheet. The **hours** are #314's gates on `POST …/time`:
  `time.entry.write` (`:any` for anyone but the caller), the `time` sku writable, every id one of
  the org's staff — asked before anything is written.
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
once the minutes exist — every part of them, each addressed by the index the document numbered
it with. Every id is grounded in what the model was shown, a due date is bounded, and minutes not
yet written are not invented whatever the answer says. The page's unsaved edits are flushed
before the model reads it, so the box changes what the reader sees.

**The recorder is told when the minutes are in** (`meeting.ready`, emitted by the worker the
moment the draft lands on `ready`, deduped per run so a redraft is heard too). Immediate in the
app, and — the one event that mails by its own default (`EMAIL_DEFAULT_ON_EVENTS`) — by mail,
because the person waiting for it recorded from a phone and walked out of the room; a person or
an org switches it off in the matrix like any other row.

## From the desk: a task with schakl's draft, the hours, and a name

**An action item becomes a task the way an approved e-mail does** — *Taak maken met schakl*
beside the item opens a sheet (`MeetingTaskSheet`) that asks `POST
/meetings/{id}/action-items/draft-task` for the task's whole form: the title, what exactly is to
be done, the steps the meeting enumerated, the deadline that was *spoken* ("voor het eind van
de maand" beside the minutes' own date), who took it on — grounded in the transcript around the
moment the item was said (`taskdraft._excerpt`), through `core/ai/taskdraft.draft_from_call`'s
own per-type grounding, with the client and project pinned to the meeting's. The reviewer reads
it beside the quote, corrects it, and `POST …/action-items/task` writes it in one call (steps and
links included, the dictation's shape) through the tasks module's own service as the reviewer.
The item then carries `task_id` and the contact moment lists the task at once; a second press
over the same item is a 409, and a save of the minutes that drops the link gets it back, matched
on the item's words rather than its position (a reordered list must never hand one item another
item's task). This is the **only** way an action item becomes a task — the checkbox that made
them in bulk on confirm is gone with the confirm. The page flushes its unsaved edits before the
sheet opens, so the item the API reads is the item on the screen. A draft the provider refuses
still opens the form, filled from the minutes, and the sheet says which of the two happened.

**The hours have their own button, and every entry is on the dialog before the press.**
*Uren registreren* (`POST /meetings/{id}/time`, `MeetingLogTime`) names the colleagues (the
staff at the table, each a choice — the entry lands on *their* timesheet), a duration (the
recording's own unless typed) and a line (the minutes' `time_note`, one sentence the model
writes for a timesheet, unless typed). Each entry goes through `time.system.record_entry`, typed
after the contact moment's kind (#182) and filed on it (a meeting not yet filed is filed first;
where that is refused the hours still land, unfiled); the gates are #314's — `time.entry.write`
(`:any` for anyone but the caller), the `time` sku still writable (a 402, because a ride-along
must never be the one way an uncovered module is written to), every id one of the org's staff —
and they are asked *before* anything is written. The record then says whose hours were booked,
by name (`time_entries` on the detail, `time_entry_ids` on the row), because a time entry
somebody did not type is a surprise on their timesheet unless the record says so.

**A meeting left unnamed is named twice, and a typed name is never touched.** The recorder's
title box may be left empty: the API names the row after the client and the day
("Bespreking met Nova Fietsen · 23-09-2026", `meetings.title.auto*`, the org's language and
calendar) and marks it `title_auto`; when the minutes land, a row still marked takes the
model's `title` once — and the screen draws a ✦ beside a title schakl chose. Editing the title
(on the page, or in the minutes' own title box) clears the mark.

**A browser's recording is remuxed once so it can be scrubbed.** `MediaRecorder` streams a WebM
whose header says *unknown duration* and carries no cues, so `<audio>` reported `Infinity`,
drew no total and could not be seeked. The fold now runs the file through ffmpeg with `-c copy`
(`pipeline.remux`, WebM and Ogg only — a phone's m4a already states its length), which writes
the duration and the cues; the measured length also fills `duration_seconds` where the recorder
sent none. Where ffmpeg is absent the bytes are kept as they came and the page uses the known
workaround (seek past the end, then back) and prints the length beside the player either way.

**The desk's controls sit where the reader is.** Opslaan and Bevestigen ride a sticky bar at
the top of the minutes (a long set of minutes ended in the two buttons that matter, a screen
below the last open question), and the AI box is the first card on the right, in the brand's
tint, with the field taking the width and the microphone inside it.

## The page: one mode, saved by itself

The detail page has no review desk and no record view: the minutes are edited where they are
read, whenever they exist, by whoever holds the write key. Every edit is **saved by itself** — a
debounced `PUT /meetings/{id}/minutes` a moment after the last keystroke, with one state word
beside the title (*Opslaan…* / *Opgeslagen* / *Niet opgeslagen* with a retry), flushed before a
navigation, before the AI box reads the meeting and before the task sheet opens, and sent
`keepalive` on the browser's own exits — because a document with a Save button at the foot is
one that is read once and corrected never, and a dropped edit is the one thing an autosave must
never do quietly. The filing (client, project, kind) saves on pick. The *Vastgelegd* card beside
the minutes states three facts with one control each: the contact moment (a link, or the button
that files it where the automatic filing was refused), the tasks made from action items, and the
hours (with *Uren registreren*). Nothing on the page decides something on a later press.

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
