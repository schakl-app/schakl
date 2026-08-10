# Notifications — the delivery model

> How an event becomes a message, on which channel, and *when*. Issues #16 (in-app + prefs),
> #17 (external transports), #245 (per-event e-mail), #283 (cadence everywhere), #295 (one
> routing table for every channel), #309 (browser push + quiet hours).
> Read this before touching `apps/api/app/modules/notifications/`.
> Browser push has its own file for the RFCs and the service worker: `docs/WEBPUSH.md`.

## The two halves

**What happened** and **who hears about it** are separate. A module emits an event
(`app/core/events.py`); the notifications fan-out decides the recipients and writes one
`notifications` row per recipient — that table *is* the in-app bell. Every other channel is a
**push channel** that rides the same emit transaction and writes `notification_deliveries` rows,
never a provider call. The worker cron drains those rows off the hot path.

No channel does network I/O inside a request. That rule is why a Slack outage cannot slow down
saving a task.

## The five channels

| Channel | Row it writes | Who it reaches | Where its routing + cadence live |
|---|---|---|---|
| `in_app` | `notifications` (`visible_at`) | one recipient | that user's matrix, per event |
| `email` | `notification_deliveries` | one recipient | that user's matrix, per event (#245) |
| `web_push` | `notification_deliveries` | one recipient, on **every device they registered** | that user's matrix, per event (#309) |
| `external`, org channel | `notification_deliveries` | a shared room | the **org** matrix, per event *per channel* (#295) |
| `external`, personal channel | `notification_deliveries` | the channel's owner | that **user's** matrix, per event *per channel* (#283) |

`in_app`, `email` and `web_push` are **implicit**: every member has them, and there is no row to
create. `web_push` has *devices* rather than an address — `push_subscriptions`, deliberately not
`notification_channels`, because a browser mints a subscription, it rotates, and it dies with a
`410`; as a channel row an ordinary auto-prune would delete a user's channel and its routing.
Its delivery row is written **per recipient, never per device**: the cadence belongs to the
person, and the fan-out to their browsers happens in the sweep, against whatever devices exist
then. `docs/WEBPUSH.md` has the rest.
An `external` channel is an explicit `notification_channels` row holding an Apprise URL,
encrypted at rest. `user_id NULL` makes it an org/shared channel; `user_id` set makes it personal.

**Every channel is routed the same way, and the scope is the only difference** (#295). A shared
room used to route by two columns of its own — `event_filter` for which events, `digest` for how
often — while a personal channel routed per event from its owner's matrix. That split is what made
"group Slack the way e-mail groups" impossible: a room had exactly one cadence for everything it
received, and the matrix had no column to say otherwise. Both now carry
`notification_preferences` rows keyed by `channel_config_id`; a personal channel's are its owner's
(`user_id` set), a shared room's are the org's (`user_id IS NULL`).

Which scope owns a channel is what decides *where you configure it*, and it is a real distinction:
*when a shared room hears about things* is one answer for the whole agency — you do not want two
admins fighting over whether `#crm` is noisy, nor whoever last opened their own settings deciding
it. *When my own Slack DM pings me* is a personal preference, exactly like the bell and my e-mail.
So each of the two settings screens shows exactly the channels it routes, and both call the list
**Kanalen**:

| Screen | Matrix | Its channels |
|---|---|---|
| Instellingen → Meldingen | my own preferences | my own transports (every member) |
| Instellingen → Standaard meldingen | the org defaults | the org's shared rooms (admin) |

`notification_channels.event_filter` and `.digest` still exist and are **no longer read** — the
#295 migration copied their meaning into org-scope preference rows and left the columns alone so
an unattended upgrade can roll back. Dropping them is the contract half, a later release
(`docs/WORKFLOW.md`).

## Cadence → `deliver_after` → one bundled message

Every cadence (`immediate`, `hourly`, `daily`, `weekly`) is expressed the same way, by
`prefs.compute_visible_at`: it turns "daily at 08:00" into the next 08:00 **on the org's own
clock** (`org_settings.timezone`, CLAUDE.md §8 — the zone is passed in, never read from a constant),
doing **wall-clock** arithmetic so a digest does not drift an hour across a DST change.

- The in-app channel writes that instant to `notifications.visible_at` — the bell simply does not
  count a row until its slot passes, so the day-grouped list *is* the digest. No digest cron, no
  synthetic row.
- Every pushed channel writes it to `notification_deliveries.deliver_after`. The worker sweep
  holds the row until the slot passes, then sends **everything due for that group as one message**.

Grouping is what makes a digest a digest, and the group key differs per sweep:

```
dispatch_email_deliveries    → group by notifications.user_id      (a person has one inbox)
dispatch_webpush_deliveries  → group by notifications.user_id      (…spread over their devices)
dispatch_external_deliveries → group by delivery.channel_config_id (a room has no single recipient)
```

Grouping a shared room by recipient would be meaningless — the whole point of a room is that
several people's notifications land in it. Both sweeps share one combiner,
`external.build_digest_message`, which renders each event's sentence in the target locale, appends
its deep link, and returns a `RenderedMessage`: chat transports send `body` (a multi-line body is
fine for Apprise), e-mail sends `html` wrapped in the org's branded chrome at the send seam
(`docs/EMAIL.md`). A group of one keeps its own sentence as the title; several fall back to the
counted digest subject.

**`immediate` therefore bundles within one cron tick.** In practice that is a group of one, and it
is exactly how personal e-mail has behaved since #17 — but it is a real (intended) property, not
an accident: two events emitted in the same second on an immediate channel arrive as one message.

## Failure handling

A send failure keeps the whole bundle `pending`, records the provider's own error on every row of
it, and rides the shared exponential backoff (`_backoff_ready`: 1, 2, 4, 8 … minutes off
`updated_at`) until `MAX_ATTEMPTS`. The rows settle together because they left together — half a
digest sent is not a state worth modelling.

Web push settles on one extra rule, because it is the one channel with several destinations per
bundle: **it is sent if any one device accepted it** (a dead phone and a live laptop is a person
who was reached), and a `404`/`410` **deletes the subscription without counting as a failure**.
A device somebody threw away is not a delivery error, and spending attempts on it would
eventually fail the bundle for the devices that are alive.

## Quiet hours

`quiet_hours_start` / `quiet_hours_end` on the scope's general row were collected from #16 and
read by **nothing** until #309 — the settings hint said so out loud. A bell that interrupts
nobody could afford that; a phone that buzzes at 03:00 cannot.

`compute_visible_at(..., quiet_hours=True)` now moves a slot landing inside the window to the
moment it ends, on the org's own clock, wall-clock (so 07:00 is still 07:00 on the two days a
year the clocks move) and handling the window that wraps midnight as well as the one that does
not. It is opt-in **per channel** and the asymmetry is the point: every *pushed* channel passes
it (`email`, `external`, `web_push`); the **bell does not**, because holding an in-app row back
interrupts nobody and makes the app look broken — you would open it, see nothing, and be told
about it in the morning.

The window is one answer per person, so it lives on the in-app general row wherever the channel
is, and `_load` fetches it in the same query as the implicit channel's own rows: honouring it
costs no extra round trip.

**Whose window an external channel obeys follows the channel's ownership**, and it has to be
resolved rather than assumed: a *personal* transport (my Slack DM) obeys its owner's window, a
*shared room* obeys the org's — a room has no single person whose night it is. `_merge_channel`
folds it in from one batched lookup. Getting this wrong is invisible: passing `quiet_hours=True`
with a window that always resolves to `None` changes nothing, and "nothing was held back" looks
exactly like the behaviour before quiet hours existed. `tests/test_notification_web_push.py`
asserts the resolved pref carries the window on the e-mail *and* the channel path for that reason.

## Routing

- **Every channel:** the per-event preference row for *that channel*, in the matrix of the scope
  that owns it. One mechanism, so there is one place to look when something did not arrive.
  No row means **not routed**, so a freshly connected channel is silent until someone says
  otherwise: connecting a transport must not start pinging a phone (or a room) on its own.
- **Everything external is a subset of in-app.** E-mail and every channel fan out from the freshly
  written bell rows, so an event switched off in-app never leaves the app, whatever its own column
  says. For a shared room that means the *org default* in-app row: nobody receives it in-app,
  nothing reaches the room.
- **Which notification a delivery hangs off** is the one thing still not a preference. A personal
  channel takes its owner's row; a shared room takes the first of the batch, because the room's
  one message stands in for the whole audience rather than for a recipient.

One nuance worth knowing before you go looking for it: a channel splits its cadence from its
*schedule*. The cadence (off / immediate / hourly / daily / weekly) is per event, in the matrix;
the schedule (which hour, which weekday its digests land on) is one choice per channel, stored in
that channel's own `digest_time` / `digest_weekday` and edited on the channel itself. Asking "at
what time?" on each of twenty-odd matrix rows would be a question with one answer.

## Who may configure what

| Permission | Held by | Covers |
|---|---|---|
| `notifications.channels.manage` | admin | the org's shared channels — and, being a superset, everyone's |
| `notifications.channels.manage_own` | admin + member | the caller's own personal channels |

Routing a shared room takes `channels.manage` **as well as** `defaults.manage` (#295): what lands
in `#crm` is an administrative act, and a tenant that handed one of those two keys to a role
without the other meant it. Both are admin-only by default, so this refuses nobody who could route
a room before. The org-default endpoint declares `defaults.manage` and `router._manages_channels`
refines it — CLAUDE.md §15's two-layer rule, again.

**Seeing the columns and writing them is deliberately one predicate**, and that is load-bearing
rather than tidy. The channel blocks are wholesale, so a caller shown no columns posts an empty
list; if that were allowed to write, someone with only `defaults.manage` would un-route every
shared room by saving an unrelated in-app default. Hence `replace_overrides(channel_events=None)`
— "leave this scope's channel rows alone" — which is a different instruction from `()`, "route
nothing", what a reset means.

Every channel route declares the `manage_own` floor and the service refines with the row in hand
(CLAUDE.md §15's two-layer rule). A member listing channels sees only their own; an org channel or
a colleague's is a **404**, never a 403 — a 403 confirms that the thing exists to anyone who
guesses an id. A member's create is forced onto their own `user_id`, so there is no body they can
send that produces a shared channel.

`manage_own` is a **new catalog key** rather than a scope on `manage`, and that is load-bearing:
the startup reconciler grants keys an org has never been offered, so re-scoping the existing key
would have changed no stored grant anywhere and every already-installed org's members would have
been left unable to connect a channel — with no data migration able to help, because a migration
must never import the catalog (`docs/WORKFLOW.md`).

## Where the code lives

| File | What |
|---|---|
| `events.py` | the event/entity/channel/cadence vocabulary — the single source of truth |
| `defaults.py` | the hardcoded bottom layer of preference resolution |
| `prefs.py` | resolution (default ← org row ← user row) and `compute_visible_at` |
| `service.py` | the fan-out: who receives an event |
| `channels.py` | the channel registry |
| `external.py` | Apprise, SSRF guard, rendering, both digest sweeps |
| `channel_admin.py` | channel CRUD, URL normalization, encryption, test-send, ownership scoping |
| `jobs.py` | the per-org ARQ cron that runs the sweeps |

On the web side, `PreferenceMatrixForm.svelte` renders the matrix (one column per channel, added
by data alone — a new channel adds no code), `ChannelSection.svelte` is the connect/test/edit
surface, and `channels.server.ts` holds the form actions behind it. All three are scope-agnostic:
each settings page mounts them with its own scope and its own channel list, which is the whole of
what the two screens differ by.
