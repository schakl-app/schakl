# Notifications — the delivery model

> How an event becomes a message, on which channel, and *when*. Issues #16 (in-app + prefs),
> #17 (external transports), #245 (per-event e-mail), #283 (cadence everywhere).
> Read this before touching `apps/api/app/modules/notifications/`.

## The two halves

**What happened** and **who hears about it** are separate. A module emits an event
(`app/core/events.py`); the notifications fan-out decides the recipients and writes one
`notifications` row per recipient — that table *is* the in-app bell. Every other channel is a
**push channel** that rides the same emit transaction and writes `notification_deliveries` rows,
never a provider call. The worker cron drains those rows off the hot path.

No channel does network I/O inside a request. That rule is why a Slack outage cannot slow down
saving a task.

## The four channels

| Channel | Row it writes | Who it reaches | Where its cadence lives |
|---|---|---|---|
| `in_app` | `notifications` (`visible_at`) | one recipient | that user's matrix, per event |
| `email` | `notification_deliveries` | one recipient | that user's matrix, per event (#245) |
| `external`, org channel | `notification_deliveries` | a shared room | **the channel row** (#283) |
| `external`, personal channel | `notification_deliveries` | the channel's owner | that user's matrix, per event *per channel* (#283) |

`in_app` and `email` are **implicit**: every member has them, and there is no row to create.
An `external` channel is an explicit `notification_channels` row holding an Apprise URL,
encrypted at rest. `user_id NULL` makes it an org/shared channel; `user_id` set makes it personal.

The split is deliberate. *When a shared room hears about things* is a property of the room — you
do not want two admins fighting over whether `#crm` is noisy. *When my own Slack DM pings me* is a
personal preference, exactly like the bell and my e-mail, so it belongs in my matrix.

## Cadence → `deliver_after` → one bundled message

Every cadence (`immediate`, `hourly`, `daily`, `weekly`) is expressed the same way, by
`prefs.compute_visible_at`: it turns "daily at 08:00" into the next 08:00 in `Europe/Amsterdam`,
doing **wall-clock** arithmetic so a digest does not drift an hour across a DST change.

- The in-app channel writes that instant to `notifications.visible_at` — the bell simply does not
  count a row until its slot passes, so the day-grouped list *is* the digest. No digest cron, no
  synthetic row.
- Every pushed channel writes it to `notification_deliveries.deliver_after`. The worker sweep
  holds the row until the slot passes, then sends **everything due for that group as one message**.

Grouping is what makes a digest a digest, and the group key differs per sweep:

```
dispatch_email_deliveries    → group by notifications.user_id      (a person has one inbox)
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

## Routing

- **Org channel:** `event_filter` (empty = every event) decides what reaches it.
- **Personal channel:** the owner's per-event preference decides — `event_filter` is not consulted.
  Two routing mechanisms on one channel would be two places to look when something did not arrive.
  No row means **not routed**, so a freshly connected channel is silent until its owner says
  otherwise: connecting a transport must not start pinging someone's phone on its own.
- **Everything external is a subset of in-app.** E-mail and every channel fan out from the freshly
  written bell rows, so an event switched off in-app never leaves the app, whatever its own column
  says.

One nuance worth knowing before you go looking for it: a personal channel splits its cadence from
its *schedule*. The cadence (off / immediate / hourly / daily / weekly) is per event, in the
matrix; the schedule (which hour, which weekday its digests land on) is one choice per channel,
stored in that channel's own `digest_time` / `digest_weekday` and edited under **Instellingen →
Meldingen → Mijn kanalen**. Asking "at what time?" on each of twenty-odd matrix rows would be a
question with one answer. `NotificationChannelConfig.digest` is therefore read only for *org*
channels; on a personal one it stays at its default and means nothing.

## Who may configure what

| Permission | Held by | Covers |
|---|---|---|
| `notifications.channels.manage` | admin | the org's shared channels — and, being a superset, everyone's |
| `notifications.channels.manage_own` | admin + member | the caller's own personal channels |

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
by data alone — a new channel adds no code), and `ChannelSection.svelte` is the connect/test/edit
surface, rendered twice: once as "Mijn kanalen" for everyone, once as the admin's shared channels.
