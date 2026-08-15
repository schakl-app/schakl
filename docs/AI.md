# AI

How the AI core works, what each feature costs, and the two rules that are easy to break.
Provider settings, the encrypted key and the usage meter are epic #131 (issues #126–#130); the
quick-add rebuild and dictation are #246.

Everything lives in `apps/api/app/core/ai/`. No feature talks to a provider SDK — they all go
through `AIService`, so the tenant's provider choice, key, per-feature toggles and budget apply
everywhere at once.

## Providers

| Provider | Chat | Speech-to-text |
|---|---|---|
| `anthropic` | yes | **no** — Anthropic has no transcription endpoint |
| `openai` | yes | yes (`POST {base}/audio/transcriptions`) |
| `openai_compatible` | yes | yes, if the tenant's server implements it |

That table is the reason speech has its own credential (see below) rather than reusing the chat
one. `anthropic` is the settings page default, so "reuse whatever is configured" would leave the
typical tenant unable to dictate at all.

Two adapters over raw `httpx`, normalised to one event stream (`providers.py`). The default
models are starting points the tenant can overwrite with free text — never a hardcoded list that
rots.

**One shared client per process.** `providers.client()` returns a keep-alive
`httpx.AsyncClient`, closed on lifespan teardown. A multi-round tool loop used to open a fresh
one per round and pay a TLS handshake each time. The tests close it between cases
(`tests/conftest.py`): an httpx client outlives the event loop that made its connections, and
reusing one across loops fails in ways that look nothing like their cause.

## The rule that matters most: hand back the DB connection

A request is **one transaction pinning one pooled connection** (`app/db.py`). A model call takes
seconds; a tool loop, tens of seconds. Holding the connection across it drains the pool and
every other request queues on checkout — which reads as *the whole site* freezing, not as one
slow feature. `docs/PERFORMANCE.md` has the general form; this is the biggest instance of it in
the codebase.

`AIService.complete()` wraps its provider turn in `ctx.release_db()`. That is the right seam
because `complete` drains the stream itself and runs no caller code inside the block, so nothing
touches the session while it is unbound. Gating happens *before* the block (it needs the
session); usage is accumulated and written *after* it (a write inside would be committed by the
block's entry).

`AIService.stream()` is deliberately **not** wrapped: its callers meter per round, and
`release_db` commits. Wrapping it is a genuine follow-up, not an oversight.

## Metering

`AIUsage` stores counts and labels only — never prompt or completion content (#126,
non-negotiable). Two units, two columns:

- `tokens_in` / `tokens_out` — text. Summed by `monthly_token_budget`.
- `audio_seconds` — transcription. Its own column and its own cap
  (`monthly_audio_seconds_budget`), because an audio model reports no token usage and folding
  seconds into the token counters would corrupt both the budget and the settings meter.

A multi-round feature accumulates on the service and writes **one** row via `flush_usage()`,
from a `finally` — tokens spent by a loop that failed halfway are still spent. Totals are
unaffected either way; both consumers `SUM`.

## The quick-add parse (#129, #246)

`POST /api/v1/ai/time/parse` turns one line of Dutch or English into a **draft** entry that
prefills the form. It never creates anything.

The pipeline is three steps:

1. **Prefetch** (`candidates.py`). Before the model is called, the server resolves what the
   tenant actually has: companies / projects / tasks matching the line's name-ish tokens, plus
   what *this user* logged in the last 30 days, plus the org's entry-type keys. Every query goes
   through the module's own `TenantScopedRepository`, so RLS and the company-group horizon (§15)
   apply exactly as they do to the find tools, and each block is gated on the same permission
   its find tool declares.
2. **One forced round.** The shortlist goes in the prompt and the model *chooses*. The find
   tools stay on the request as a fallback for what the shortlist missed, and the prompt tells
   the model to issue any lookups in the same turn. `_PARSE_MAX_ROUNDS = 2`: one free round,
   then a forced `submit_time_entry`.
3. **Ground the answer.** An id the model was never shown is dropped, never guessed.

> **The one change that would silently sink this**: `_checked_uuid` validates against
> `_seen_ids(tool_texts) | candidates.ids()`. Drop that union and *every* correctly chosen id
> comes back null — an HTTP 200 that looks exactly like the model having no tools at all.
> `test_time_parse_grounds_on_the_prefetched_shortlist` exists for that, beside the #129 test it
> must not weaken. The shortlist is **additive grounding**, never a relaxation.

The recent-usage query is what earns the design its keep: name matching is a bare ILIKE with no
fuzzy fallback, so "Jansen" typed as "Jansn" matches nothing — but the client you booked hours
to on Tuesday is almost certainly the one you mean today.

### Why the parse fills only some fields

`billable` is **tri-state**. `None` means the text said nothing, which is what lets the form keep
the project's own default (#284). A `False` would be indistinguishable from the user having
said "niet declarabel". Same discipline everywhere: unstated is null, never a default.

`entry_type_key` is a tenant-defined slug, not a UUID, so `_seen_ids` cannot vouch for it —
membership in the org's active keys is its grounding (`_checked_key`).

**Not in the parse: subscription/agreement.** `TimeEntry.subscription_id` is legacy and no
longer written; #225 removed the picker because a covered project's budget *is* the retainer's
included hours. An entry links to a project.

### "Today" is the org's today

Relative dates resolve against `org_today(session, org_id)`, and the client sends the day it is
actually looking at (`TimeParseRequest.today`). Both matter: UTC made "gisteren" a day early for
several hours every night, and answering with the server's today navigated the user off the day
they were working on.

## Speech to text (#246)

`POST /api/v1/ai/time/transcribe`. See `docs/VOICE.md` for the whole feature; the AI-core parts:

- Its own provider config on `ai_settings` (`speech_*`), because of the table at the top.
  `NULL` means "reuse the chat provider", which only resolves for one that can transcribe.
- `enabled_features()` appends a `speech` **capability** (not an `AI_FEATURES` toggle) when
  `_speech_ready()` holds, so the web gate never draws a microphone that would 409. Same helper
  backs `AISettingsRead.speech_available`, so the settings screen and the control agree.
- `transcribe.py` is a **third top-level provider function**, not a branch in `stream_chat`: the
  request is multipart and the reply is one JSON object, so it shares neither the SSE reader nor
  the event normalisation the rest of `providers.py` is built around.
- Rides the existing `time_assist` feature key. Adding a fifth `AI_FEATURES` entry would touch
  eight places including two hand-maintained web copies.

## Email into task (#327)

`email_assist` reads an approved email into the task it was filed onto: notes, a checklist, a
deadline, the links the work needs, and whether closing it needs an answer to the sender.
Opt-in per approval (`InteractionApprove.enrich_task`), off by default. It lives in
`app/modules/interactions/enrich.py` — the module owns its prompt and its grounding, exactly as
`reporting` does — and writes through `tasks/system.py`, never `TaskService`.

### What real mail changed

Three faults, and the first two are one rule twice: **the screen around the task already answers
this, so writing it again is not thoroughness, it is noise.**

**Notes are the few lines that let someone act.** They arrived as a provenance header (sender ·
subject · date), then a paragraph naming the sender, then the message retold one bullet per
sentence, then a line stating what the mail did *not* say. All four are on the interaction, which
is linked to the task and one click away. The header is gone outright; the other three are
refused **by name** in the prompt, because the instruction that was already there — "never
restate the whole email" — described the fault without naming any of its shapes, and a model
cannot avoid a shape it has not been shown.

**Grounding answers forgery, not relevance, and only one of those was ever a problem.** "The URL
must appear in the body" is honest about a footer link: it *is* in the body. So one mail put
eight links on a task of which three were the work — the sender's homepage, their Google review
invitation, their terms page, a contact page, and the Calendly embed's own `widget.js`. The
second question is asked structurally (`_is_boilerplate_url`), for the reason a filter usually
is: a model obeying "only what appears in the message" is being obedient, and being wrong. It is
answered by **what the URL points at** — a bare host (a name, not a destination), a standing page
(`voorwaarden`, `privacy`, `contact`, `review`, `unsubscribe`, …), a social/map/profile host, an
asset extension — and never by locating a signature block, because no boundary survives the
HTML→markdown conversion, an inline footer with no `--` delimiter, and a forwarded thread
carrying two of them, whereas what boilerplate *points at* is the same in all three.
`MAX_EMAIL_LINKS = 4`, far under the seam's own ten: a link panel is a shortlist of what to open,
and past a handful nobody opens any of them.

**The comment is off the vocabulary.** It only ever restated the notes a paragraph later, most
often as "the sender asks nothing further" — a conclusion the model is deliberately unable to act
on, since status is not on `TaskEnrichment` and never will be ("this is resolved, close it" is
the first sentence a hostile email would try). Closing the task with this contact moment stays
the *other tick in the same dialog* (`close_task` → `Task.closing_interaction_id`), where a
person makes it. A field whose only possible content is a duplicate or an unactionable verdict is
noise, and the approve dialog never advertised one either.

Three things make it different from every other feature here, and all three follow from one
fact: **its input is written by someone outside the organisation.**

**Nobody waits.** The body does not exist when the task is created — a pending row is metadata
only, and the gmail fetch happens after the approving transaction, deliberately. So the approve
flips `tasks.ai_status` to `queued` and a deferred ARQ job does the reading, re-deferring on a
widening ladder while the body has not landed and ending as `skipped` when it never does. The
card polls `GET /tasks/{id}/ai-status` — one column, its own endpoint precisely so the poll does
not drag the whole card — and calls `invalidateAll()` exactly once, when there is something new
to draw. A quarter-hourly reaper ends runs whose worker is gone: the #300 rule, because the row
cannot tell a busy worker from a dead one.

**The prompt is not the defence.** `_INJECTION_STANCE` is stated in this feature's own terms,
and it is a request rather than a control. What actually bounds the damage:

1. **One forced tool and nothing else on the request** — no find tools, no write tools. The
   model's whole output channel is `submit_task_plan`'s fixed schema.
2. **A narrow vocabulary** (`TaskEnrichment`). Assignee, client, project, status and above all
   `visible_to_client` are *not on it*, so a fully compliant model obeying a hostile email still
   cannot move work to another client or hand an internal task to a client portal.
3. **Links are grounded in the message.** A URL the model proposes must appear in the body. Be
   precise about what that buys: it does **not** make a link safe — whoever wrote the email chose
   its links, and carrying them over is the feature. It guarantees nothing is added that was *not
   in the message*, which is what stops an invented address landing on a colleague's board. The
   boilerplate filter above sits *beside* it and answers a different question; neither substitutes
   for the other.
4. **Our own markup is stripped** before storage (`tasks/system._untrusted_markdown`, using
   `MENTION_RE` itself so the strip cannot drift from the extractor that finds them). An email
   must not be able to make the platform notify anyone.
5. **The writes are conservative wherever they are irreversible**: the description is appended,
   never replaced; a due date only fills a blank, and only inside a bounded window;
   `requires_interaction` is one-way (adding a guard is safe to be wrong about, removing one a
   person asked for is not); and the notes are capped at `MAX_SUMMARY_CHARS` so a runaway answer
   is a bounded one.

**Its own toggle, against this file's own advice.** Riding an existing key is usually right, and
here it is not: this is the only feature that sends a *client's own words* to a model, and an
agency happy for AI to polish a colleague's paragraph may well not be happy for it to read the
mailbox. Folding it into `writing_assist` would have switched the second on for everyone who had
already agreed to the first.

## Model choice

`DEFAULT_MODELS` seeds the settings form; a per-feature override lives in the `features` JSONB.
The parse asks for `max_tokens=1024` — a draft entry is a dozen short fields, and the 8192
default is sized for a written report.

A tenant who cares about quick-add latency should set a fast model for `time_assist`
specifically and leave the org default alone. There is no reasoning-effort knob yet: `effort`
400s on some models and the model field is free text, so it needs a retry-without-it fallback
first.

## Adding a feature

1. A key in `AI_FEATURES` (and its two web copies — `settings/ai/+page.svelte`,
   `+page.server.ts`, plus `AIFeature` in `lib/core/ai/index.ts`). Weigh this: riding an
   existing key is often right.
2. A function in `features.py` taking `AIService`.
3. A route declaring `require_permission("ai.use")` — §15 is enforced by
   `tests/test_rbac_deny_by_default.py`, so a missing declaration is a build break. A row-shaped
   rule (like transcribe's `time.entry.write`) goes in the service, not the decorator.
4. A prompt in `prompts.py`. Every prompt carries `_INJECTION_STANCE`: record content reaches
   the model as data inside JSON, never as instructions.
5. Tests via the `_fake_stream` monkeypatch of `providers.stream_chat`, plus a `count_queries`
   budget if the feature loops.
