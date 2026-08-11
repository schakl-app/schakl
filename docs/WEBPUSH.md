# Browser push notifications (Web Push)

> The fifth delivery channel in `apps/api/app/modules/notifications/`: the browser's own
> Web Push (RFC 8030/8291/8292), reaching a phone or a closed laptop lid. Issue #309.
> Read `docs/NOTIFICATIONS.md` first — this is an extension of that model, not a new one.

## 1. What already existed, and what it meant for this

The delivery model was already the right shape and this channel changes none of it:

- a module emits → the fan-out writes one `notifications` row per recipient (the bell);
- every non-bell channel writes a `notification_deliveries` row **inside the emit transaction**
  and does no network I/O in the request;
- the per-org ARQ cron (`jobs.py`) drains those rows, groups them, renders one bundled message
  per group with `external.build_digest_message`, and settles the whole bundle together with
  exponential backoff.

So the web-push channel is: a `NotificationChannel` implementation, a sweep function next to
`dispatch_email_deliveries` / `dispatch_external_deliveries`, and a place to keep the devices.
Everything about *routing*, *cadence*, *digests*, *locale*, *deep links*, *backoff* and *per-org
timezone* was already written and is reused verbatim.

Three facts from the existing code shaped the whole design:

1. **`build_digest_message` was transport-agnostic already.** It returns `title` / `body` / `html`
   with absolute deep links off `brand.base_url`. A push payload is `title` + `body` + a URL.
   Nothing new to render.
2. **The cron ticked once a minute**, so an "immediate" push would land up to 60 s late (§9).
3. **`quiet_hours_start` / `quiet_hours_end` were stored and never read.** `compute_visible_at`
   ignored them, and `notifications.settings.quiet_hint` in `nl.json` said so out loud
   ("Wordt bewaard, maar nog niet toegepast"). A bell that does not interrupt could afford
   that. A phone that buzzes at 03:00 cannot. See §8.

## 2. Why this is not an Apprise `external` channel

Apprise 1.12 (already a dependency) *does* ship a `vapid://` Web Push plugin. It is the wrong
tool here on both counts:

- **Mechanically**: it loads subscriptions from a `subscriptions.json` file and its VAPID key from
  a PEM file on disk, keys targets by e-mail address, POSTs to a fixed per-browser URL
  (`fcm.googleapis.com/fcm/send`) instead of the subscription's own `endpoint`, and is
  `requests`-synchronous.
- **Conceptually**, which is the part that would still be true if the plugin were perfect: a push
  subscription is **not something a person types**. It is minted by a browser, it is per *device*
  not per person, it rotates without warning, and it dies with a `410 Gone`. Modelling it as a
  `notification_channels` row would put a machine-generated credential into a screen built for
  "paste your Slack webhook", and would make an ordinary auto-prune silently delete a user's
  channel — along with the `notification_preferences` rows keyed to it, and therefore their
  routing.

So: **an implicit channel** (like `in_app` and `email` — every member has it, there is no row to
connect) **with a device table behind it**. `notification_channels` is untouched.

What Apprise *is* useful for is `apprise/utils/pem.py::encrypt_webpush`, a correct, standalone
RFC 8291 `aes128gcm` implementation over `cryptography` (already a dependency) that needs no
keyfile. It is a private module of a dependency pinned `>=1.12.0` with no upper bound, so it is a
**reference to check ours against**, not something to import. See §6.

## 3. Data

Two new tables in the notifications module, org-scoped and RLS-forced like every domain table.

```
push_subscriptions
  id, org_id, user_id            -- a device belongs to a person within a tenant
  endpoint      TEXT NOT NULL    -- the push service URL the browser minted
  p256dh        TEXT NOT NULL    -- the client's public key (base64url)
  auth          TEXT NOT NULL    -- the client's auth secret (base64url)
  user_agent    TEXT             -- so "Chrome op Windows" is namable in the UI
  last_seen_at  TIMESTAMPTZ      -- refreshed whenever the browser re-presents it
  last_success_at, failure_count
  UNIQUE (org_id, endpoint)

push_vapid_keys
  org_id UNIQUE, public_key TEXT, private_key_enc TEXT   -- app.core.crypto
```

`p256dh`/`auth` are **not** secrets of ours — they are the recipient's public key material, and
the payload is encrypted *to* them. They need no encryption at rest. The VAPID private key does.

**The keypair is per org, generated lazily on first use.** The alternative — two env vars and a
keygen command — means the feature is silently off on every unattended upgrade (`docs/WORKFLOW.md`),
which is the worst of the available failure modes. Per-org costs nothing (a subscription is bound
to an origin and each org has its own hostname anyway), needs no instance-level table, and rides
the tenancy layer we already have. It is **never rotated**: rotating invalidates every existing
subscription, since a browser binds its subscription to the `applicationServerKey` it was created
with.

## 4. The channel

```python
CHANNEL_WEB_PUSH = "web_push"   # events.py, beside CHANNEL_IN_APP / _EMAIL / _EXTERNAL
```

`WebPushChannel.deliver` mirrors `EmailChannel.deliver` almost exactly: resolve this event's
web-push preference for the whole batch in one query, and write **one delivery row per
recipient** — not per device.

### The default: on for the urgent events, silent for the rest

`prefs.web_push_default(event_type)` is the bottom layer of the three-layer resolution, and it is
**not** a single constant: an event whose *in-app* cadence is `immediate` is pushed, an event that
lands in tomorrow's 08:00 digest is not.

It is derived from `default_event_pref(event).digest` rather than from a second list, so the two
definitions of "this is urgent" cannot drift apart when an event is added.

The first cut had it uniformly off, reasoning by analogy with an external channel (#283: connecting
a transport must not start pinging a phone). The analogy does not hold, and the owner reversed it.
A Slack webhook is a URL somebody pastes with no further ceremony; a push subscription is minted by
a **browser permission dialog that names notifications**, which is the opt-in — asking for it and
then delivering silence makes the success state of the feature indistinguishable from its failure
state, and sends the user to a second screen nobody told them about. What the split preserves is
the part that was actually right: a phone lighting up to deliver something whose own cadence is
"tell me tomorrow" is how a channel gets switched off for good.

A default is what applies when nothing has been said, so it never overwrites: an org row or a user
row that turns an event off outranks it by construction (user ← org ← default). Migration
`b3f6c1d80a45` additionally writes the same answer down as **org-default rows** for orgs that
existed at the time — never touching an org that had already said something — so an upgraded
instance shows the setting as its own rather than as an inherited constant.

One row per recipient, not per device, because the cadence belongs to the *person* and the devices
are an implementation detail of reaching them. Per-device rows would turn a daily digest of ten
events into ten × three messages and make "did this get delivered?" a question with three answers.
The fan-out to devices happens in the sweep, at send time, against whatever devices exist *then*.

`deliver` also skips recipients with **no** device (one batched `WHERE user_id IN (...)`, within
the fan-out's query budget — `EmailChannel` already does one), so dead rows never accumulate for
the majority of users who never grant permission.

`NotificationDelivery` needs no new column: `channel = 'web_push'`, `channel_config_id` stays
`NULL`, `deliver_after` carries the cadence slot exactly as e-mail's does.

## 5. The sweep

`dispatch_webpush_deliveries(session, org)` in `external.py` (or a new `webpush.py` — see §12),
called from `jobs._dispatch_for_org` beside the other two.

- `_due(CHANNEL_WEB_PUSH, org.id, now)` — the existing query, unchanged.
- Group by `notifications.user_id`, like e-mail: a person has devices, not the other way round.
- `build_digest_message(...)` in the recipient's locale — reused as-is.
- POST the encrypted payload to **every** live subscription of that user.

Settling rules, which is where this channel differs from the others:

- **The bundle is `sent` if any one device accepted it.** A user with a dead phone and a live
  laptop was reached; failing the bundle would re-send to the laptop on the next tick.
- **`404` / `410 Gone` deletes the subscription row and does not count as a failure.** A retired
  device is not an error, and burning attempts on it would eventually fail deliveries for the
  devices that *are* alive. This is the auto-prune the whole model depends on.
- **`429` and `5xx` ride the existing `_backoff_ready` backoff**, with the provider's status in
  `last_error` like every other channel.
- **A `413` means the payload was too large** — treat as a permanent failure of that bundle and
  log it; it is a bug in our truncation, not a transient condition. See §7.

## 6. The wire format (what actually has to be written)

Three small pieces, all on `cryptography` — **no new Python dependency**:

1. **VAPID JWT** (RFC 8292): ES256 over `{aud: <scheme://host of the endpoint>, exp: now+12h,
   sub: mailto:<org contact> }`, sent as `Authorization: vapid t=<jwt>, k=<public key>`. ~25 lines.
2. **Payload encryption** (RFC 8291, `aes128gcm`): ephemeral P-256 key, ECDH against `p256dh`,
   two HKDF derivations against `auth` and a random salt, AES-GCM, then the `aes128gcm` record
   header. ~50 lines; `apprise/utils/pem.py::encrypt_webpush` is a correct reference to diff against.
3. **The POST**: `httpx` (already a dependency, already async), with `TTL` and `Urgency` headers.

Owning ~75 lines of well-specified crypto beats adding `pywebpush` (synchronous, `requests`-based,
would need `asyncio.to_thread` inside an otherwise async worker) — and beats importing a private
module of an unpinned dependency. It goes in `app/core/webpush.py` with its own unit tests, and
the RFC test vectors are the test.

**The endpoint is attacker-supplied and must be SSRF-guarded.** It arrives from a browser, but the
API cannot tell a browser from a client with a `curl` command, so an unguarded endpoint lets a
signed-in user aim the worker at an internal address. `app.core.net_guard.is_public_address` and
the `check_url_safe` pattern already exist for exactly this: require `https`, resolve the host, and
refuse a private/link-local/loopback target — **at subscribe time *and* again at send time**,
because DNS can rebind between the two. `SCHAKL_ALLOW_PRIVATE_NOTIFICATION_TARGETS` covers the
self-hosted-behind-a-proxy case, as it does for webhooks.

The payload itself is JSON:

```json
{ "title": "...", "body": "...", "url": "https://.../tasks/<id>", "tag": "...", "count": 3,
  "icon": "https://.../icon-192.png", "badge": "..." }
```

`icon`/`badge` are **URLs in the payload**, resolved from the tenant brand at send time — never
baked into the service worker (Golden Rule 4). The push service cannot read any of this: it is
encrypted end-to-end to the browser's own keys.

## 7. The service worker

`apps/web` had **no custom service worker**: `vite.config.ts` runs `SvelteKitPWA` in its default
`generateSW` mode, so workbox generates the whole thing at build time and we never see it. Push
needs our own `push` and `notificationclick` listeners inside it. Two ways in:

**(a) `workbox.importScripts: ["/push-sw.js"]` + `apps/web/static/push-sw.js`** — what we did.
The generated SW `importScripts` our ~40 lines at install. The precache manifest, the runtime
caching and `registerType: "autoUpdate"` are all untouched, so the blast radius is one new static
file. Cost: plain JS, outside the bundle, not typechecked.

**(b) `strategies: "injectManifest"` + `apps/web/src/sw.ts`** — full TypeScript, typechecked,
bundled. Cost: we now own the caching strategy workbox was writing for us, in an app that already
ships as an installed PWA to real users. That is a regression surface disproportionate to two
event listeners.

Revisit (b) only if the service worker ever needs to do something substantial.

The handlers:

- `push` → `event.waitUntil(self.registration.showNotification(title, {...}))`. **Always show
  something**: a push that shows no notification is a spec violation and browsers revoke permission
  for it. A `tag` makes a second push *replace* rather than stack. Optionally
  `navigator.setAppBadge(count)`.
- `notificationclick` → focus an existing client on the same origin if there is one, else
  `clients.openWindow(url)`. Focusing beats opening a second tab of an app someone already has open.

**No service worker runs in dev** (`devOptions` is off), so this is only exercisable against a
production build — `vite build && vite preview`, or the real image. A `pnpm dev` session will
never show you a push, and that is not a bug to chase.

### Nothing installed the worker, in any environment

The section above describes a worker that gets *generated*. It never described one that gets
*installed*, and for the whole first life of this feature nothing did: `injectRegister: "auto"`
places its `<script src="/registerSW.js">` through Vite's `transformIndexHtml` hook, and
SvelteKit bakes `app.html` itself without ever calling that hook. The build emitted `sw.js`,
`push-sw.js` and `registerSW.js`; the server served all three; no page referenced the third; no
browser was ever asked for a worker. Not in dev — where it is expected — but also not in preview
and not in the production image. `curl`ing the deployed login page found zero occurrences of
`registerSW` or `sw.js` in the HTML, while `/sw.js` answered `200`.

Every symptom pointed somewhere else. Push was the only feature that *asks* whether a worker
exists, so the entire fault surfaced as one sentence on one settings screen —
`settings.push.no_worker`, *"de achtergrondservice die de app installeert, draait niet"* — which
reads like a browser problem and was a build-wiring problem. Meanwhile the PWA half of it (the
precache, `autoUpdate`, offline assets) had simply never worked and nothing was watching.

The fix is `apps/web/src/lib/core/pwa.ts`, called from the **root** `+layout.svelte`; the
reasoning, including why the path must be absolute and why `skipWaiting`/`clientsClaim` and
`navigateFallback` are now stated explicitly, is in `docs/WEB.md`. Two rules are worth carrying:

- **When a surface only exists because something else wires it up, assert the wiring.** This is
  `docs/MCP.md`'s `/api/docs` lesson in a second costume: there, every test called
  `app.openapi()` in-process, which is exactly the path the bug was not on. Here, every test
  stubbed `navigator.serviceWorker`, which is exactly the object that did not exist.
  `pnpm pwa:check` reads the build output and fails when no bundle registers a worker.
- **A plugin option that silently does nothing is worse than a missing one.** `injectRegister`
  was set, was correct for the plugin it belongs to, and had no effect in this framework. It is
  `false` now, with the reason written at the call site.

The `ready` timeout below is a different bug, found earlier and fixed on its own. It is what
turned this one from an eternal spinner into a sentence — and, having a plausible sentence for
it, is also part of why it went unexamined for so long.

`navigator.serviceWorker.ready` resolves once a worker is active and
has **no other outcome**: with nothing registered for the scope it stays pending for the life of
the page, never rejecting, so the `try`/`catch` around it caught nothing and `status()` never
returned. The settings section sat on `common.loading` forever — in every dev session, and in any
real tab whose registration had failed or been unregistered by hand. `ready` is therefore **raced
against `REGISTRATION_TIMEOUT_MS`, never awaited bare**, and running out is an answer rather than
an error: `no-worker` is its own state with its own sentence, because `off` would draw an Enable
button that asks for permission and then fails on the missing worker (#253), and `unsupported`
would blame a browser that supports this perfectly well. The generalisation is worth keeping: **a
promise that only ever settles on success needs a floor, not a `catch`** — and a bug whose only
symptom is a spinner is invisible to every functional test, which is why
`apps/web/tests/unit/push-status.test.ts` pins it with a `ready` that never settles.

## 8. Quiet hours become real — and that is the point

`quiet_hours_start` / `quiet_hours_end` were collected in the settings UI, resolved into
`ResolvedPref`, and read by **nothing** from #16 until this issue; the Dutch hint said so out loud.
A bell that interrupts nobody could afford that. Shipping push without fixing it would have meant
the first tenant to enable it being woken by a task comment at 03:00 — by a setting that was
already on their screen.

`compute_visible_at` now moves a slot landing inside the window to the moment it ends, on the
**org's** clock, with the same wall-clock arithmetic the digests use (so it does not drift across
a DST change), handling the window that wraps midnight as well as the one that does not. A
wrap-only implementation silently never fires for `12:00–13:00`; both are tested.

It is opt-in **per channel**, because `compute_visible_at` is shared and a blanket change would
silently reschedule e-mail and Slack as a side effect. Every *pushed* channel passes
`quiet_hours=True` (`email`, `external`, `web_push`); `in_app` does not — the bell interrupts
nobody, and holding a bell row back would make the app look broken. **This changes e-mail and
chat behaviour for existing tenants who filled the field in**, which is a release note, and
`notifications.settings.quiet_hint` was rewritten in both locales to stop calling itself unused.

**Passing the flag is not the same as honouring it**, and that is the trap worth naming. The flag
reads the window off the `ResolvedPref`, so a resolution path that never fills those two fields
turns `quiet_hours=True` into an expensive no-op — and one that no functional test can see,
because "nothing was held back" is exactly the old behaviour. The external channel had precisely
that shape until `_merge_channel` learned to fold the window in (a *personal* transport takes its
owner's, a *shared room* the org's — a room has no single person whose night it is). The test
therefore asserts the **resolved preference carries the window** on each path, not merely that
each call site passes the argument.

## 9. Latency

The cron was `cron(dispatch_notification_deliveries, second=30)` — one tick a minute, so an
"immediate" push arrived up to 60 s after the event. Acceptable for a digest; it is the difference
between a notification and a reminder for `task.mentioned`. It is now `second={0, 15, 30, 45}`,
so ≤15 s. That multiplies the empty-sweep cost by four — three cheap indexed queries per org per
tick, one per pushed channel, each returning nothing when there is nothing to do.

The alternative, enqueueing an ARQ job from the request, was rejected: it breaks the "no I/O in
the emit transaction" rule that keeps a Slack outage from slowing down saving a task.

## 10. Permissions, portal, and who may subscribe

**No new permission key.** Registering my own browser is the same act as reading my own inbox:
`notifications.notification.write` (held by admin, member **and** client) already covers it.

Deliberately *not* `notifications.channels.manage_own`: that key exists to gate a URL a person
types, with an SSRF surface behind it. A push subscription is minted by the person's own browser
and points at Google, Mozilla or Apple.

The consequence, deliberate rather than accidental: **a client-portal login may register a device
at the API layer**, for their own notifications. That follows the role defaults that already
govern their inbox instead of adding an exception to them.

**There is no portal screen for it, though**, and that is worth knowing before someone calls it a
bug: the portal shell hides the bell and the whole settings area (`isPortal` in the app layout),
so `PushSection` is staff-only in practice. Giving clients a way in is a portal-UX decision, not a
permissions one, and the permission is already in the right place for whenever that is made.

Cross-tenant leakage is structurally impossible: a service worker is scoped to an origin, each org
has its own hostname, so a browser signed into two orgs holds two independent subscriptions in two
`push_subscriptions` rows.

## 11. The settings surface

A `PushSection.svelte` on Instellingen → Meldingen, beside `ChannelSection` (which routes
*external* channels and is not the right home for a device). The matrix gains a `web_push` column
next to in-app and e-mail — that part is API work (`PreferenceRow` carries `email_*` explicitly, so
it is not "data alone" on the API side, whatever the web component manages).

Rules for the section, all of which are ordinary UX mistakes worth stating before someone makes one:

- **Never auto-prompt.** `Notification.requestPermission()` on page load is the dark pattern
  browsers penalise. It sits behind a button that says what it is for.
- **`denied` is not our state to fix.** Say it is the browser's setting and that we cannot reopen
  the prompt — do not render a button that always refuses (#253).
- **iOS needs the app installed.** Safari supports Web Push from 16.4 **only inside a
  home-screen-installed PWA**. Detect the display mode and say "voeg schakl eerst toe aan je
  beginscherm" instead of offering a button that silently fails.
- **Re-present a granted subscription on app load.** Endpoints rotate, and a rotated endpoint is a
  silent death — the user sees nothing and assumes it works. Only when `Notification.permission ===
  "granted"`, so it costs one call per session and none for anyone else (`docs/PERFORMANCE.md`).
- **List the devices** with their `user_agent` and last-seen date, each revocable. "Which of my
  four browsers is this?" is otherwise unanswerable.
- A **test-push** button, mirroring the channel test-send that already exists.

Every string in `messages/en.json` **and** `messages/nl.json` in the same change (Golden Rule 2),
and remember Paraglide here does not parse ICU plurals — use `_one` key pairs.

## 12. Files

| File | Change |
|---|---|
| `app/core/webpush.py` | **new** — VAPID JWT, RFC 8291 encryption, the async POST, the SSRF guard |
| `modules/notifications/models.py` | `PushSubscription`, `PushVapidKey` |
| `modules/notifications/events.py` | `CHANNEL_WEB_PUSH` |
| `modules/notifications/webpush.py` | **new** — `WebPushChannel` + `dispatch_webpush_deliveries` |
| `modules/notifications/prefs.py` | resolve/merge/write the web-push column; quiet hours in `compute_visible_at` |
| `modules/notifications/schemas.py` | `push_*` on `PreferenceRow`/`Write`; subscription schemas |
| `modules/notifications/router.py` | `GET /push/config`, `POST /push/subscriptions`, `GET`, `DELETE /{id}`, `POST /push/test` |
| `modules/notifications/__init__.py` | `register_channel(WebPushChannel())` |
| `modules/notifications/jobs.py` | call the third sweep |
| `alembic/versions/a9d3f4b81c62_notifications_create_web_push.py` | the two tables + RLS |
| `apps/web/static/push-sw.js` | **new** — `push` + `notificationclick` |
| `apps/web/vite.config.ts` | `workbox.importScripts`; later: `injectRegister: false`, explicit `skipWaiting`/`clientsClaim`, `navigateFallback: null` (§7) |
| `apps/web/src/lib/core/pwa.ts` | **new** (follow-up) — registers the generated worker, which nothing did |
| `apps/web/src/routes/+layout.svelte` | calls it once, at the root, for the whole origin |
| `scripts/pwa-check.mjs` | **new** (follow-up) — the build-output guard; `pnpm pwa:check`, CI after the web build |
| `apps/web/src/lib/modules/notifications/PushSection.svelte` | **new** — enrol / list / revoke / test |
| `apps/web/src/lib/modules/notifications/push.ts` | **new** — subscribe/unsubscribe/refresh |
| `PreferenceMatrixForm.svelte`, `prefs.server.ts`, `(app)/+layout.svelte` (the session refresh), `messages/*.json`, `docs/NOTIFICATIONS.md` | extend |

## 13. Tests

`tests/test_notification_web_push.py`, beyond the standard tenant-isolation and deny-by-default
sweeps:

- **the fake transport must refuse a bad credential everywhere** — the `docs/CLOUDFLARE.md`
  lesson: a fake that always succeeds means the only test that could catch a real failure passes
  against a provider that does not exist;
- **`410` prunes the row and does not burn an attempt** (and the next tick does not retry it);
- **one delivery row per recipient regardless of device count** — a `count_queries` budget, because
  this is exactly the shape that is one query at three devices and one-per-device at three hundred;
- **N events on a daily cadence → one push**, not N;
- **quiet hours**: a 03:00 slot moves to the window's end on the org's clock, and survives a DST
  boundary — using `tests/conftest.org_today()`, never `date.today()`;
- **the SSRF guard refuses a private endpoint** at subscribe *and* at send;
- RFC 8291 test vectors against `app/core/webpush.py`.

## 14. What is not built

- **Notification action buttons** ("Markeer gelezen" without opening the app). They need an
  authenticated `fetch` from inside the service worker and are a genuinely separate design.
- **A portal-facing surface** (§10): the permission allows it, no screen offers it.
- **Nothing has been exercised against a real push service.** Every test here monkeypatches
  `webpush.send`, so what is proven is the encryption (against the RFC's own vector), the
  routing, the settling and the pruning — *not* that Google's FCM accepts our VAPID header. The
  checklist for the day someone runs it for real is one item long: build the image, open the
  settings screen in Chrome and in Firefox, enable, press **Test versturen**, and confirm the
  notification appears and clicking it focuses the tab. A `401`/`403` from the push service on
  that first attempt would point at `vapid_headers`; anything else at the payload.
  **That checklist has still not been run**: the §7 fix means the screen now offers an Enable
  button where it used to refuse — verified in Chromium against the real adapter-node build, where
  the worker installs, imports `push-sw.js` and `status()` answers `off` — but no notification has
  yet made the round trip through Google or Mozilla.

## 15. Decisions taken, and why

1. **VAPID keys per org, generated lazily** — not two env vars. Env vars mean the feature is
   silently off after an unattended upgrade, which is the worst failure mode available: nobody
   finds out until they wonder why nothing arrives.
2. **Quiet hours apply to every pushed channel**, not to web push alone. More correct, and it
   changes e-mail and chat behaviour for existing tenants who filled the field in — a release note.
3. **Client-portal logins may register a device** at the API layer; no screen offers it (§10).
4. **The cron ticks four times a minute.**
5. **Web push offers the full cadence set** (`off` / `immediate` / `hourly` / `daily` / `weekly`).
   A "daily digest push" is arguably a mail, but the machinery is shared and free, and the column
   is consistent with every other one — the tenant decides.
