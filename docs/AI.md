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
- `transcribe.py` is a **third top-level provider function**, not a branch in `stream_chat`: the
  request is multipart and the reply is one JSON object, so it shares neither the SSE reader nor
  the event normalisation the rest of `providers.py` is built around.
- Rides the existing `time_assist` feature key. Adding a fifth `AI_FEATURES` entry would touch
  eight places including two hand-maintained web copies.

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
