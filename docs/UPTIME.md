# Uptime Kuma integration

> The `uptime` module: monitors, groups and default settings on the tenant's **own** Uptime Kuma
> instances, plus the status those instances report back. Business-licensed (`sku="uptime"`).
> Read this before changing anything under `apps/api/app/modules/uptime/`,
> `apps/web/src/lib/modules/uptime/` or `apps/web/src/routes/(app)/settings/uptime/`.

Sibling to `docs/CLOUDFLARE.md`, and deliberately built to the same three rules: the credential is
a row, an integration that mirrors outside state stores *what it decided* and *what it last
observed* in separate columns, and a health probe is evidence rather than the gate. Where this doc
repeats one of them it is because the failure it prevents is the same failure, not because the
paragraph was copied.

**All four gates are built.** What follows describes the code, not a plan; where building
changed a decision the section says so. The one thing to know before reading further is that
*three* separate bugs in this module came from the same place — see §3's *"a commit expires
every row"*.

**§2's checklist has been run against a live Uptime Kuma 2.5.0** (`docker run -e
UPTIME_KUMA_DB_TYPE=sqlite louislam/uptime-kuma:2.5.0`). What it found is recorded there as
observed fact, and several things it found had already been written down wrong — including one
argument in §3 that was right for the wrong reason. Items that still need a *tunnelled* or
*2FA-enabled* instance are marked as unrun.

## 1. What it is for, and what it replaces

Issue #96 (part of #87, milestone P4) already reserved this ground, and reserved it the wrong way
round. Its plan was *"turning on a website's uptime toggle fires a `webhook.post` to the n8n →
Uptime-Kuma flow"*, on the same reasoning as its redirect half: **keep us out of talking to the
provider directly.** That argument has already been overturned once. The redirect half became the
`cloudflare` module (epic #278), which talks to Cloudflare directly, owns the rule it created, and
can tell you when somebody changed it underneath us — none of which a fire-and-forget webhook into
n8n can do. The uptime half fails for the same reasons and gains the same things by being a module.

So `Website.uptime_enabled` finally gets a mechanism. It has existed since #94 as a boolean whose
own comment reads *"The uptime webhook (a later automation slice) acts on this flag"* — a flag with
nothing behind it, exactly as `Domain.status = redirect` was before #278. This module is what acts
on it. **Reuse the flag; do not add a second one.**

What an agency gets that an n8n recipe could not:

- **The monitor is a record.** It has a client, a website, a profile, an activity trail, and a place
  on the company page next to the domain and the hosting account it depends on.
- **Defaults are configurable per tenant** (§8) instead of frozen into a flow nobody edits.
- **Drift is expressible** (§6). Somebody *will* edit a monitor in Kuma's own web UI, because Kuma
  has one and it is good. A webhook that fires once cannot notice; a mirror can.
- **Instances we cannot reach are still first-class** (§4, §5), which is the case an outbound
  webhook recipe never had an answer for.

## 2. There is no REST API, the wrapper is two majors behind, and that is why we vendor a client

### The HTTP surface does not exist

At tag `2.5.0`, `server/routers/api-router.js` exposes `/api/entry-page`, six `/api/badge/:id/…`
routes, and nothing else. `server.js` mounts one further route, `/metrics`, behind `apiAuth` —
`express-basic-auth` with an API-key authorizer (`server/auth.js: verifyAPIKey`). **Every monitor,
group, notification, tag, proxy, maintenance and status-page write is a Socket.IO event.** A
wrapper is not a convenience layer here, it is the only door.

`/metrics` is worth knowing about anyway: it is stable, HTTP, documented, authenticated by a
revocable API key, and it carries per-monitor status and response time. It is a legitimate
*read-only* fallback for a `linked` instance whose owner will hand out an API key but not an admin
password. It cannot configure anything.

### The wrapper is two majors behind

| | |
|---|---|
| Uptime Kuma stable | **2.5.0**, 1 Aug 2026. 2.x has been stable since 2.1.3 (Feb 2026). |
| `lucasheld/uptime-kuma-api` | **1.2.1**. Last push **5 Apr 2024**. 394 stars, 37 open issues. Supports **UK 1.21.3 – 1.23.2**. |
| `exaland/uptime-kuma-api-v2` | **1.0.3**, 26 May 2026. 9 stars. Claims **UK 2.0.0-beta.2**. |

The fork is a **51-line diff** against the abandoned original — three fixes, all in the status-page
path, plus a `conditions` parameter. It ships **the same 22 monitor types** the 2024 original did.
Uptime Kuma 2.5.0 has **33**, so eleven are unreachable through either library:

`globalping`, `manual`, `ntp`, `oracledb`, `pm2`, `rabbitmq`, `sip-options`, `smtp`, `snmp`,
`system-service`, `websocket-upgrade`

(No type goes the other way: everything the library knows still exists in 2.5.0.) The fork also
installs under the **same import name** as the original, `uptime_kuma_api`, so the two can never
coexist in one virtualenv — a dependency that can silently become the other one.

### Therefore: `client.py` is ours

`app/modules/uptime/client.py` is a Socket.IO client this repo owns, the same posture as
`app/modules/cloudflare/client.py`. Both libraries are MIT (© 2023 Lucas Held) and Uptime Kuma
itself is MIT, so the derivation is clean — keep the notice in a `LICENSE` beside the module, as
`cloudflare/` already does.

This is not the ban CLAUDE.md §11 places on writing an integration from memory. It is the OXXA
situation with better sources: the two libraries' source *and* Kuma's own `server.js`,
`server/socket-handlers/` and `server/auth.js`, which are the authoritative document. What we gain
is the thing a pinned dependency cannot give: UK 2.6 adding a monitor type becomes a one-line change
here instead of a wait on somebody's inbox, and the eleven missing types are reachable on day one.

Rules the client does not bend, all four inherited from `cloudflare/client.py`:

- **The credential never reaches a log line, an exception message, or a response.**
- **Nothing is deleted at Kuma that schakl did not create.** Every destructive call takes a
  `kuma_monitor_id` this module stored earlier.
- **The network is off in tests.** One transport seam; unset, a test that forgot to install the fake
  fails loudly on connect.
- **Read-then-write, never blind write.** `edit_monitor` sends the full monitor object back, so a
  field we do not model would be silently reset to a default. Round-trip the payload Kuma returned
  and overwrite only the keys we own — the fork learned this the hard way on status pages, where
  rebuilding a v1-era field set made 2.x answer *"Invalid analytics type"*.

### What a live 2.5.0 actually did

Run against `louislam/uptime-kuma:2.5.0`. Fourteen findings, of which **four contradict something
written from the source alone**, and one is a state neither wrapper models at all.

**0. A fresh 2.x is not reachable over socket.io until its database wizard is answered.** This
phase does not exist in 1.x. `server/setup-database.js` runs first, logs *"Waiting for user
action…"*, and until it is satisfied the process serves **the SPA's HTML, with HTTP 200, for every
path — including `/socket.io/`**. A naive reachability probe reads that as healthy. Setting
`UPTIME_KUMA_DB_TYPE` (also `_DB_HOSTNAME` / `_DB_PORT` / `_DB_NAME` / `_DB_SOCKET`,
`UPTIME_KUMA_ENABLE_EMBEDDED_MARIADB`) skips it. This is the sharpest argument for §5's
proof-of-identity gate: *200 with a body is not proof of anything*, and a client's half-installed
Kuma is a realistic thing to be pointed at.

**1. python-socketio throws away the path you hand it, and a subpath instance is the casualty.**
`_get_engineio_url` rebuilds the request as `{scheme}://{netloc}/{socketio_path}/`, so connecting
to `https://host/kuma/socket.io/` really asks for `https://host/socket.io/`. Found by a test that
was checking something else: a client pointed at `http://localhost:3011/definitely-not-kuma`
connected happily to the Kuma at the root.

   That is worse than a failure. An agency running Kuma behind a reverse proxy on a subpath —
   the ordinary deployment — would either fail for no visible reason, or on a host serving more
   than one thing **silently reach a different instance and mirror the wrong monitors**. So
   `client.socketio_path_for` folds the subpath into `socketio_path`, the only parameter
   python-socketio honours, and the origin is passed separately. `https://host/kuma/` →
   origin `https://host`, path `kuma/socket.io`.

**2. The transport upgrades cleanly.** 5–9 ms to connect on the LAN, and poll-then-upgrade
reached `transport='websocket'` unaided. No pinning needed. *(Still unrun: through Cloudflare
Access.)*

**3. Messages are i18n keys now, and the exception proves the rule.** Kuma 2.x answers
`{'ok': True, 'msg': 'successAdded', 'msgi18n': True}` where 1.x answered *"Added Successfully."*.
So **nothing may match on English prose** — except that the rate limiter's message
(*"Too frequently, try again later."*) carries no `msgi18n` and is plain English. Code the
mismatch defensively; do not assume the flag is present.

**4. `loginRateLimiter` is 20 per minute, instance-wide — and this is why §4's token design is a
requirement, not a convenience.** `tokensPerInterval: 20, interval: "minute", fireImmediately:
true`, on a single module-level limiter shared by **every** caller of that instance. It tripped on
attempt 21 and then refused a *correct* password with *"Too frequently, try again later."*
`loginByToken` carries no limiter at all: 30 consecutive calls succeeded in 559 ms.

   The consequence is worse than §3 first claimed, and in a different direction. The risk was
   written up as schakl racing a brute-force defence; the real risk is that **a password-per-
   operation sync would deny the instance's owner access to their own Kuma.** Twenty logins is one
   modest sync run, the bucket is shared, and the human who then tries to sign in is told to come
   back later by their own monitoring. A per-operation design is not merely inefficient here, it is
   hostile to the tenant.

**5. Every JWT claim is confirmed.** Payload is exactly `{username, h, iat}` — **no `exp`**, so the
token never expires on its own. After a password change the same token answers
`{'ok': False, 'msg': 'authInvalidToken', 'msgi18n': True}` — **a distinguishable i18n key**, which
is what lets §14 map re-authentication to its own error rather than to "wrong password".

**6. `info` is emitted twice, and the version is post-authentication only.** Pre-auth it carries
`primaryBaseURL`, `serverTimezone`, `serverTimezoneOffset`. Post-auth it adds `version`,
`latestVersion`, `dbType`, `runtime`, `isContainer`. Kuma 2.x withholds its version from
unauthenticated clients.

   This splits one gate into two, and the doc previously conflated them. **Proof-of-identity** (§5)
   is "an `info` event arrived at all", available before login and the right gate for *is this
   Uptime Kuma*. **The version floor** is a separate, later check that cannot run until we are
   authenticated. Reading the first `info` and expecting a version — which is what a naive
   `api.version` does — yields `None` on every 2.x instance.

**7. A monitor round-trips 119 keys against the 16 we sent.** 103 fields we never mentioned come
back and must survive an edit. That is the whole argument for read-then-write stated as a number:
a builder that reconstructs the payload from the fields it knows about silently resets a hundred
of them. `add` also **requires `conditions`** — a 2.x `NOT NULL` column with no default — and
returns the new id under **`monitorID`**, not 1.x's `monitorId`.

   Both of those break the fork on its own claimed version: passing `conditions=None` raises
   `SQLITE_CONSTRAINT: NOT NULL constraint failed: monitor.conditions`, and its documented
   `monitorId` return key does not exist. The most basic call in the library fails against 2.5.0.

**8. `parent` is a plain integer, absent as `None` at top level**, and Kuma derives a display
`pathName` (`'rt-group / '`). Groups need no second table, as §7 assumed.

**9. Secrets come back in the clear, and there are eight of them.** `basic_auth_pass`,
`databaseConnectionString`, `mqttPassword`, `oauth_client_secret`, `rabbitmqPassword`,
`radiusPassword`, `radiusSecret`, `tlsKey` — the canary password was returned verbatim by
`getMonitor`. §4's strip-and-fingerprint is confirmed necessary, and this is the authoritative key
list to strip. (The payload also carries an `includeSensitiveData` flag, worth investigating as a
possible server-side opt-out.)

**10. `monitorList` is pushed 25 ms after connect**, keyed by id, and is never a call. The
reference client's 0.2 s `wait_events` settle is ~8× the observed arrival on a small instance;
*still unrun* at ~200 monitors, which is where a fixed settle silently truncates.

   **This finding was written down correctly and then contradicted by the code it was written
   for**, which is the most useful thing in this document. `list_monitors` called
   `getMonitorList` and read the list out of the **ack** — and the ack is a bare `{"ok": true}`.
   Measured against a live 1.23.17 holding 34 monitors: the sync reported success, created
   nothing, cleared the instance's error and set it `active`. Every screen said "connected" over
   an empty list, and no log line disagreed.

   Three things made it survive review. The docstring's rule — *every read is an acknowledged
   call, never a settled event* — was the right correction to the wrapper's fixed sleep and was
   over-applied to a read whose answer is not in the ack at all. `getTags` **does** answer in its
   ack, so the assumption was true often enough to look general. And `uptime_fake` returned the
   list in the ack too, so the only test that could have caught it agreed with the bug — the
   failure its own docstring claimed to prevent. The fake is now faithful, and
   `test_the_monitor_list_arrives_as_a_push_and_never_in_the_ack` asserts **both** halves: that
   the ack is empty, and that the client finds the monitors anyway.

**11. The same shape hides the notification channels, and `getSettings` is a decoy.**
`getSettings` acks `{"ok": true, "data": {…}}` — the instance's own settings, never the
channels. `notificationList` is pushed once, unprompted, at login, and **no event re-requests
it**: `getNotificationList` does not exist and times out. So `list_notifications`, which read
`notificationList` out of `getSettings`' ack, answered `[]` on every instance that had any
configured (two, on the instance measured). It now reads the login push, which is why it is the
one list read that issues no call.

**12. `conditions` is a 2.x column, and sending it to a 1.x is not a spare key.** 1.x `add`
imports the create payload onto the row wholesale, so the key 2.x *demands* is, one major
version down, an unknown column against the tenant's own database. The gate is therefore
version-aware and **omits it when the version is unknown**: omitting on 2.x fails cleanly,
loudly and reversibly, naming the column in the error, while the opposite mistake writes to a
schema. The fake refuses both ways round, which is what makes the gate testable.

**13. `conditions` was not the only thing `add` demands, and the second one is worse.** 2.5.0's
`add` handler dereferences `monitor.accepted_statuscodes.every(...)` **before** it branches on
type (`server.js`, two sites). Omit the key and there is no default and no validation message —
the call answers the raw JavaScript `Cannot read properties of undefined (reading 'every')`,
naming neither the field nor the module. It applies to **every** type, a `group` included, which
is the one payload nobody thinks to give status codes to. `_kuma_fields` has always sent its own,
so nothing shipped broken; `REQUIRED_ON_CREATE` now carries it as the floor under any caller that
does not, which is what the constant is for. Found by seeding a live 2.5.0 through this repo's own
client — the general lesson being that a create payload built from the fields *we* model is tested
only where a caller happens to supply the rest.

Still unrun, and each needs an instance we do not have yet: **2FA** (`{tokenRequired: true}` and
what a wrong TOTP looks like), **a subpath reverse proxy**, **Cloudflare Access end to end**,
**`/metrics` with an API key** for the `linked` path, **`jwtSecret` rotation without a database
edit**, and **the settle at scale**.

## 3. A blocking socket in an async API

The reference client connects **in its constructor**, `sio.call()` blocks, and `_get_event_data` is
a `while … time.sleep(0.01)` poll followed by a fixed `wait_events` settle, *because there is no way
to know when the last message of a type has arrived*. One `get_monitors()` therefore costs a
handshake, a login round-trip, a list event and a 0.2 s settle — call it 0.3–1.5 s against a healthy
instance, and up to the full timeout against a sick one, which is precisely when somebody is
staring at the screen.

Three rules follow.

- **Every call runs in a thread and inside `ctx.release_db()`.** CLAUDE.md §3 already states this for
  `app/core/ai/`; the reason is identical and the pressure is worse. A socket round-trip holds a
  pooled database connection for seconds while doing no database work, and §11's pool-drain is what
  takes the whole API down rather than one screen.
- **The request path almost never talks to Kuma.** Reads come from the mirror (§6). Sync and import
  are ARQ jobs through `run_per_org`. Only a single-monitor create/edit/pause goes inline, with a
  timeout short enough to fail a button rather than a page.
- **Connect per operation, disconnect in `finally`.** A long-lived socket is tempting — heartbeats
  arrive pushed, for free — and it is wrong here. The API rolls `start-first` on two replicas
  (`docs/DEPLOY.md`), so a persistent socket means two of them, two event caches and two opinions
  about what is current; reconnection is the reference client's weakest code; and a proxy in front
  of the instance will idle-timeout the connection anyway. Per-operation also keeps `disconnect()`
  in the one place it can be guaranteed, which the library's own docstring insists on.

### A commit expires every row, and this module commits in the middle of its writes

The single most expensive lesson here, and it bit three times before it was understood.
``release_db()`` **commits** on entry — that is how it hands the pooled connection back — and a
commit expires every loaded ORM object. Worse, ``TimestampMixin.updated_at`` carries
``onupdate=func.now()``, a **SQL** expression, so *any* flush that updates a row expires it
again. Serialising that row afterwards lazy-loads from inside Pydantic, synchronously, in a
context with no greenlet, and asyncpg answers ``MissingGreenlet``.

Ordinary write paths never notice, because their flush is the last thing that touches the row
before the response. This module's are not: an external call sits in the middle of several of
them, and the row crosses that seam twice.

Two rules, and the second is the one that is easy to miss:

* ``_settled(row)`` — flush, then re-read — is the **last statement of every write path that
  returns a row**. It lives on the base service, because the plain instance edit needs it too:
  there the expiry is caused by the flush alone, with no socket involved at all.
* **Never carry an ORM row across a commit into code that reads it.** The webhook's
  announcement takes a plain dict captured *before* the commit, which is also simply the right
  shape for it — an announcement should not depend on live rows.

Assigning to an attribute hides the problem, because that un-expires it without loading. That is
why the instance paths appeared to work for two gates: ``_mark()`` assigns every field it
touches, and nothing read ``updated_at`` until a test finally PATCHed an instance.

The cost of per-operation is an authentication per operation, and **that is what makes §4's cached
token a requirement rather than a convenience.** Measured on 2.5.0: `loginRateLimiter` allows
twenty logins per minute, `fireImmediately`, on one module-level bucket **shared by every caller of
that instance**. It tripped on the twenty-first attempt and then refused a correct password.
`loginByToken` passes through no limiter at all — thirty consecutive calls in 559 ms.

So a design that re-sent a password on every connect would not merely be slow. One modest sync run
spends the whole instance's login budget, and **the next person to sign into their own Uptime Kuma
is told to come back later by their own monitoring.** Enrol once, `loginByToken` thereafter, and
per-operation connect costs nothing anyone else can feel.

## 4. The credential is a row, and `mode` decides what the row means

`uptime_instances` holds one row per Uptime Kuma server. A per-org singleton is wrong on day one for
the same reason it was wrong for Cloudflare accounts: the agency runs one for itself, and a client
may run their own that we are asked to look after.

`mode` is the first field, because it decides which of the others mean anything.

| | `managed` | `linked` |
|---|---|---|
| We hold a session token | yes (never a password — see below) | **no** |
| Outbound reach required | yes | **no** |
| Read monitors, import, mirror | yes | via `/metrics`, if an API key was given |
| Create / edit / pause monitors | yes | no |
| Receives status | webhook **and** poll | webhook only |
| Status is | observed | *reported by the instance* (§11) |

**`linked` is not a degraded `managed`.** It is the mode for a client-hosted instance behind a
firewall whose owner will never give us a tunnel, and it delivers most of the value — a status
timeline on the client's page, an alert, an automation trigger — at no infrastructure cost
whatsoever, because the traffic runs the other way (§11). Designing as if `managed` were the only
real mode is what produces a feature that works in a demo and for one client.

Columns, for a `managed` row: `base_url`, `username`, `token_encrypted`, `ssl_verify`,
`connect_headers`. For either: `webhook_secret`, `status`, `server_version`, `last_error`,
`last_checked_at`, `last_synced_at`.

- **Everything secret is Fernet at rest** (`app.core.crypto`, the `*_encrypted` convention from
  `docs/GOOGLE.md`) and **write-only through the API**: the response carries `token_configured`,
  never a value.
- **`connect_headers` is a JSONB of request headers, encrypted, and it is the entire tunnel
  feature** (§5). A Cloudflare Access service token, a proxy's shared secret, a `Host` override are
  all the same field with different values. It is encrypted for the same reason the token is: an
  Access service token is a credential, and it must never reach a log line, an error message or a
  response.

### We store a token, never a password, and never a TOTP seed

The obvious shape — store `password_encrypted`, log in per operation (§3) — was wrong, and the
version of it with a stored TOTP seed was worse. 2FA on Kuma's `login` event is a TOTP in
`data.token`, so an unattended cron against a 2FA-protected instance could only work by holding the
seed. Holding both factors is not two-factor authentication; it is one factor and a filing cabinet.

Kuma's own token machinery makes that unnecessary. Read from `server/model/user.js` at 2.5.0:

```js
static createJWT(user, jwtSecret) {
    return jwt.sign({ username: user.username, h: shake256(user.password, SHAKE256_LENGTH) }, jwtSecret);
}
```

No `exp`. And `loginByToken` verifies the signature, then re-derives `h` from the **live** password
hash and refuses a mismatch, and loads the user with `active = 1`. So:

- **Enrolment is interactive and happens once.** An admin enters the Kuma username, password and —
  if 2FA is on — the current TOTP code. We call `login` once, keep the returned JWT in
  `token_encrypted`, and **discard the password and the code without ever writing them**.
- **Everything after that is `loginByToken`.** The cron holds no password and no seed. There is
  nothing at rest that could reconstruct either.
- **It is still password-equivalent in power**, so it is encrypted, never logged and never returned.
  What it is not is password-equivalent in *blast radius*: it authenticates one account against one
  instance, and it cannot be replayed anywhere else.
- **Three revocations, all of them the client's** — change the Kuma password, deactivate the Kuma
  user, or rotate `jwtSecret`. That is a better answer than "delete the row in schakl and hope",
  and it is the sentence to put in the docs an agency hands a client.
- **A dead token is a state, not an error.** `status = needs_reauth`, the settings screen asks for a
  code, and the mirror keeps rendering (§6). Treating it as *credential invalid* would send an admin
  to rotate something that was never wrong; treating it as *unreachable* would blame the tunnel.

The cost is honest and small: a Kuma password change means one admin re-enrols. That is precisely
the event that *should* require re-authorisation.

### There is no service account to ask for, and that is why `linked` exists

Uptime Kuma's entire user surface is three socket events — `setup`, `needSetup`, `changePassword`.
There is no create-user, no roles, and one code path logs in with `R.findOne("user")` and no
predicate at all. **The instance has one account, and it is the owner's.**

So least privilege is not available to us here at any price. Whatever an agency enrols is the full
admin of that instance, and no amount of care on our side changes that. Three things follow, and
they are the reason the rest of §4 is shaped the way it is:

- **Not storing the password stops being a nicety.** It is the only reduction in blast radius that
  was ever on offer.
- **The three revocations are the client's whole recourse**, so they belong in the sentence an
  agency says when asking for the credential — not buried in our settings screen.
- **`linked` is the answer to "no"**, and it will be said. A client who declines to hand over the
  only administrator account of their own monitoring is being sensible, not obstructive, and a
  module that treats that as a configuration failure has misread the situation. They give a webhook
  URL, optionally a `/metrics` API key, and everything in §11 works.

- **Deleting an instance cascades the local rows and touches nothing at Kuma.** Deleting a client's
  live monitoring as a side effect of tidying a credential list is unrecoverable.

### Monitor payloads carry secrets, and the mirror keeps a fingerprint of them

Kuma's monitor object includes `basic_auth_pass`, `oauth_client_secret`, `radiusPassword`,
`mqttPassword`, `databaseConnectionString`, `tlsKey` and `tlsCa`. A mirror that stores the raw
payload stores all of them, in a table nothing else in this repo treats as a secret store — and
`remote_snapshot` is read by a detail endpoint, so §2's "the credential never reaches a response"
would become false the day somebody added a field.

**Strip on ingest, and keep a fingerprint.** For every secret-bearing key, `remote_snapshot` stores

```json
"basic_auth_pass": { "set": true, "fp": "<hmac-sha256, per-instance salt>" }
```

and never the value. The first draft of this section stored a bare `true` and accepted that drift
detection would go blind on those fields — which was safe and needlessly lossy. A keyed fingerprint
is safe *and* keeps the functionality: **"somebody changed this monitor's basic-auth password at
Kuma" stays detectable** by comparing fingerprints, while the value is unrecoverable from the row.

The salt is per instance and random, so the comparison only works inside the instance it was taken
from and an exported database is not an offline dictionary against every tenant at once. The
fingerprint is never returned by the API either — it is an internal comparison value, and a client
that receives it receives an oracle.

What is still lost, and should be: we cannot tell an admin *what* the new password is, and we cannot
push a secret back that we do not hold. A monitor whose secret drifted is reported as
`drift (credential)` and the reconcile offers only *Overnemen* — schakl declines to overwrite a
credential it never had.

## 5. Reaching an instance we do not host

The problem shape: schakl must open an **outbound WebSocket** to a Kuma that may be public behind
TLS (trivial), on the agency's own LAN (trivial when self-hosted, impossible on cloud), or on a
*client's* LAN (the real case).

### `app/core/net_guard.py` has already settled the LAN half

Its docstring says outright that an OIDC IdP or an OpenAI-compatible LLM endpoint *"may legitimately
live on the LAN for a self-hosted install"*, and that those paths deliberately do not call the
private-address block — they close the redirect-pivot instead. A tenant's Uptime Kuma is the same
shape as both. So `uptime` is a **net-guard-exempt outbound path with the redirect-pivot closed**,
and that is precedent rather than a new exception. It is emphatically *not* an argument to relax
`net_guard` for the webhook and notification paths, which expect a public target and keep the block.

### The exemption is not a licence: `base_url` is an SSRF surface

Exempting this path from `net_guard` means an admin can type `http://169.254.169.254/` and have the
API connect to it. Four things bound that, and the split between them is the deployment, not a
setting.

1. **On `SCHAKL_DEPLOYMENT=cloud`, `net_guard` applies in full.** There is no LAN worth reaching from
   a shared instance, so there is no functionality to trade away — and a tenant admin who could aim
   the API at the metadata endpoint of a box other tenants share is a cross-tenant problem wearing an
   integration's clothes. Only self-host takes the exemption, where the admin already owns the
   machine.
2. **Redirects are refused, not followed.** The handshake is an HTTP request before it is a socket,
   and a public URL that 302s to `127.0.0.1` is how every allow-list gets walked around.
3. **The target must prove it is Uptime Kuma before anything is stored** — and proof is *an `info`
   event arriving over socket.io*, not a version and not an HTTP status. §2 found both halves of
   why: a half-installed 2.x answers **HTTP 200 with HTML on every path**, so status proves
   nothing; and 2.x withholds `version` from unauthenticated clients, so the version cannot be the
   gate. A metadata endpoint speaks neither socket.io nor `info`. The **version floor** is a
   separate check that runs after authentication — two gates at two moments, and conflating them
   gives you one that always fails on 2.x.
4. **No response body ever reaches an error message.** `last_error` carries our own i18n key and
   Kuma's own error text, never the bytes of whatever answered. A blind port probe that echoes the
   response is not blind.

Point 3 is the one to hold on to when this gets extended: the day somebody adds a "test connection"
button that reports what came back, the first three protections stop mattering.

**`ssl_verify = false`** exists because a Kuma on a client's LAN with a self-signed certificate is
ordinary, and refusing it would push agencies to expose the box publicly instead — the worse
outcome. But it is off-by-default, refused entirely on cloud, permitted on self-host only for a
private-address target, and **badged on the instance row and in the list**, because "we send an
admin credential to whoever answers this address" deserves to be visible rather than remembered. A
stored certificate fingerprint to pin against is the better answer and the obvious follow-up; it is
not in the first gate.

### Cloudflare Tunnel + Access is the documented path

`cloudflared` runs beside the client's Kuma, opens an outbound-only connection to Cloudflare and
publishes `kuma-klantx.bureau.nl`. Access protects the hostname. We authenticate with an **Access
service token** — two headers, `CF-Access-Client-Id` and `CF-Access-Client-Secret`. No inbound port
on the client's firewall, no VPN client on ours.

The reason this is cheap rather than a project: the socket.io client takes headers and passes them
to the handshake. **The whole feature is `connect_headers`.** If the Kuma box already has a public
address, drop the tunnel and keep Access with the same service token; the tunnel is only what you add
when there is no inbound path at all.

The tunnel lives in the **client's** Cloudflare account, so it is not automatable through the
`cloudflare` module's per-tenant token — that credential is for the *client's zones*, and a tunnel is
account-level provisioning nobody has handed us. Document the four steps, do not build a wizard for
somebody else's dashboard.

### "Zero Trust in front of the API" — two readings, and only one of them is this

- **In front of the Kuma instance:** yes. That is the paragraph above.
- **In front of schakl's own API:** a different question, and it would break things. It protects
  *inbound* traffic to us and does nothing about *outbound* reach into a client's LAN. It would also
  sit on top of routes that are unauthenticated by design — the Mollie callback
  (`docs/PAYMENTS.md`), the public invoice page (#304), and this module's own webhook (§11).

### The other two mechanisms

**Tailscale / WireGuard.** Cleanest networking, heaviest operations: a userspace daemon per
container and a mesh agent on the client's box. Document it as *"if you already run a tailnet, point
`base_url` at the tailnet address and you are done"* — which is true, and is the whole integration.
Do not build it.

**A schakl-side agent on the client's network.** No. That is a second product.

### The deployment decides what is possible

On `SCHAKL_DEPLOYMENT=cloud` there is no LAN to reach, so `managed` requires a publicly resolvable
(tunnelled) URL, full stop. On self-host a LAN address is fine. This is the #137 / #253 shape: the UI
must **say** which, from the `deployment` value that already rides `/meta/tenant`, rather than draw a
control that always refuses.

## 6. Decided, observed, and drift

`uptime_monitors` splits into what the tenant decided and what Kuma last said, in separate columns.
This matters more here than for `cloudflare`, because **Kuma has a good web UI and people use it**.

- **Ours:** `name`, `type`, `url` / `hostname` / `port`, `interval`, `retries`, `retry_interval`,
  `resend_interval`, `accepted_status_codes`, `upside_down`, `expiry_notification`, `ignore_tls`,
  `notification_ids`, `parent_group_id`, `profile_id`, `active`.
- **Theirs:** `kuma_monitor_id`, `remote_snapshot` (JSONB, stripped per §4), `remote_hash`,
  `last_observed_at`, `sync_status`, `last_error`.
- **Links:** `website_id`, `domain_id`, `hosting_id` — all nullable — and `company_id` (§10).

`sync_status` is five values and not a boolean, for `RedirectStatus`'s reason: each needs a different
button.

| value | means | the button |
|---|---|---|
| `pending` | we have never pushed it | *Aanmaken in Uptime Kuma* |
| `active` | it is there and matches | — |
| `drift` | it is there and somebody changed it | *Overschrijven* / *Overnemen* |
| `missing` | it is gone from Kuma | *Opnieuw aanmaken* / *Hier ook verwijderen* |
| `error` | Kuma refused | the stored `last_error` |

`drift` offers **two** buttons on purpose. An agency editing a monitor in Kuma because that screen
was closer to hand is the normal case, not the deviant one, and a reconcile that can only overwrite
teaches people to stop using the tool they already had.

Two siblings, both already paid for once in `docs/CLOUDFLARE.md`:

- **No probe is the gate.** A failed login on instance A must not blank instance B's list, and must
  not hide A's own stored mirror. The screen keeps rendering what we last observed, with the
  *"laatst gelezen om …"* line that makes it honest. A credential check that runs first and raises
  for everything behind it turns one endpoint's opinion into a verdict on the whole integration.
- **The flag is two-way.** Whatever sets `status = error` must say what clears it — any successful
  call does — or a row nothing is wrong with keeps its red line through every sync that works. And
  the fake must reject a bad credential *everywhere*, or the only test that could catch this passes
  against an Uptime Kuma that does not exist.

## 7. Groups are monitors

`MonitorType.GROUP` is a monitor type and `parent` is a monitor id. There is no group entity. So a
group is a row in `uptime_monitors` with `monitor_type = "group"` and `parent_id` a self-FK,
resolved from `parent` at import (`_link_parents`, a second pass because a child can arrive before
its group) and back to it on write. `GROUP_TYPE` in `models.py` is the one place that word is
spelled.

### What a group has to do on a screen

The mirror holding the tree is not the same as the CRM showing it, and the first without the
second is indistinguishable from an agency that never grouped anything. Three parts, all cheap:

- **`parent_name` on the monitor read**, resolved under `meta=true` in **one** query for the whole
  page (`group_names`). Not denormalised onto the child: the group's name is Kuma's to change, and
  a copy would go stale silently. Not resolved per row either — a self-referential lookup looks
  free and is the per-row read `docs/PERFORMANCE.md` bans, so
  `test_the_monitor_list_costs_the_same_however_many_groups_there_are` pins it at two statements
  against two groups and four children.
- **`group_count` on the instance**, riding the existing count as a filtered aggregate rather than
  a second query. Groups are counted *inside* `monitor_count`, because a group is a monitor here
  and a total that quietly excluded them would disagree with the list the same screen links to.
- **A group has no `target`.** Kuma stores it a `url` of `"https://"` — its own form's
  placeholder, saved — and copying that through renders every group as a monitor pointed at a
  broken address instead of as the folder it is.

The obvious agency shape — **one group per company**, every one of that client's monitors beneath it
— is a convention this module *offers* at import and on create. It is not a schema fact. A tenant who
already groups by environment, or by datacentre, or not at all, must not have their tree rewritten by
an import, and no code may assume a monitor's group tells you its client (§10 is what tells you).

Two consequences worth knowing before writing the sync: a group's own `active` cascades to its
children in Kuma, so pausing a group pauses monitors whose own row still reads active; and deleting a
group is a question Kuma answers on its own terms, so this module refuses to delete a group that
still has children rather than discovering the answer on a client's monitoring.

### What was still missing, and what #321 added

Counting a thing is not managing it. The screen said *"3 groepen"* per instance and offered no way
to see which three, make a fourth, or rename one — so an agency that wanted its tree maintained
here went back to Uptime Kuma, and the next sync had opinions about that. Four parts:

- **Instellingen → Uptime lists the groups**, with their instance and their **child count**
  (`child_count`, folded under `meta=true` in one grouped query beside `group_names`). The count is
  not decoration: it is what makes the delete guard predictable instead of a surprise 409, and the
  delete button is drawn only where it can succeed (#253 — a control that always refuses is
  broken).
- **Create, rename, delete.** A create is `POST /uptime/monitors` with `monitor_type = "group"` —
  there is no second concept, because Kuma has none — and it pushes, because a group we made must
  exist at the far end to be a parent. A delete is local by default, exactly as a monitor's is.
- **The children guard from this section is now real** (`errors.uptime_group_has_children`). It was
  written down here and never implemented: `parent_id` is `ON DELETE SET NULL`, so deleting a group
  locally un-nested every monitor beneath it *here* while Kuma kept the tree, and nothing said when
  or why the two stopped agreeing.
- **A move made in Kuma is drift, for a monitor we created.** `_link_parents` used to overwrite
  `parent_id` from the snapshot for **every** row regardless of `adopted` — the one field the
  distinction §6 is built on was not being applied to — so somebody dragging our monitor into
  another group was absorbed in silence. It is now reported as `parent_id` in `drift_fields`, with
  both buttons the drift table already offers, and `adopt` moves the group with the rest (clearing
  the flag without moving it would raise the same drift on the next sync, which reads as a
  reconcile that did not work). A row we have **never pushed** is skipped entirely: its snapshot is
  empty, and reading a parent out of it set every pending monitor's group to nothing.

Still not here: a **group picker on the monitor itself**. The API takes `parent_id` on create and
on `PATCH`, but the web has no monitor list or monitor form yet, so there is nowhere to put the
control. It belongs with that screen, not bolted onto a settings page.

### A link you can write is a link something has to read back

The same sentence pointed at the *link* columns rather than at `parent_id`, and there it was not a
missing convenience — it was a feature with one half built. `website_id`, `domain_id` and
`hosting_id` had a matcher, a horizon, a confirm button and an activity line, and between them
exactly one way to be seen afterwards: a panel filtered on `website_id`. Four consequences, and
the first is the one an agency actually hit.

- **Confirming *"koppel aan domein acme.nl"* stored the row correctly and showed it nowhere.**
  `GET /uptime/monitors` had no `domain_id` filter, and no domain panel existed to have used one,
  so the proposal left the list and nothing took its place. That is indistinguishable from a
  button that does nothing, and it is the ordinary case rather than the exotic one: `matching`'s
  own ladder falls back to the domain for every host inside a zone we hold that will never be a
  website — a client's mail server, VPN endpoint or NAS. Both anchors now filter, and a domain
  gets the same panel a website does.
- **A monitor the matcher found nothing for could never be attached at all.** The proposals
  section was the only link surface in the product, and it lists what a sync proposed; an
  `unmatched` row appeared on no screen anywhere. So a bare IP, a host in a zone the tenant does
  not hold, or a client's Kuma naming things its own way was permanently unattachable — precisely
  the instance this module exists to adopt. The panel now carries a picker over
  `link_status=unlinked`, a *filter* value covering all three unlinked states, because "what may I
  still attach" is a different question from any one matcher outcome.
- **Nothing could be un-attached.** `POST /link` has taken an explicit `null` pair since #321 and
  no screen ever sent one, so a wrong link was permanent from the UI and the row had already left
  the only list that would have offered it again. Detach is a button on the row now. It writes
  nothing to Kuma and deletes nothing — the monitor keeps running and keeps being mirrored, it
  just stops claiming to be this record's — which is why it is an ordinary button rather than a
  confirm dialog.
- **`company_name` and `instance_name` were always `null`.** Both are declared on
  `UptimeMonitorRead`, both document themselves as resolved under `meta=true`, and nothing
  resolved either — the settings screen kept its own client-side map of instance names to work
  around the half it needed. Resolved now, one batched query each, under the same flag.

The control lives on the website and the domain rather than on a settings screen for this
section's own reason: a monitor's link is a fact about the thing being watched, so it belongs on
that thing's page. It reaches the API through the host page's form actions
(`uptime-actions.server.ts`, spread into both routes), the contract `wordpress` and `cloudflare`
already use — a panel cannot own SvelteKit actions. The picker's options are **streamed rather
than awaited**, because attaching by hand happens once per record and blocking every website and
domain page on a list most visits never open would be paying for the rare case on every read.

One rule the picker inherits from the matcher: **a group is never offered.** A group is a monitor
here (§7) and watches nothing, so attaching one would put a folder in the panel; the matcher never
proposes one because it has no target to match on, and the picker draws the same line rather than
letting the only other way in disagree.

### Attaching answers half the question; the other half is that nothing watches it yet (#366)

Both controls on the panel — *Koppel een monitor* and *Ontkoppel* — take a monitor that **already
exists in Uptime Kuma**. `POST /uptime/monitors` had existed since gate 2 and the entire web app
called it from exactly one place: `createGroup` on the settings screen, which posts
`monitor_type: "group"`. A folder, never a check.

So a website nobody has set up monitoring for — the normal state of a site delivered last week —
had no way forward from the page that is about it. You opened Uptime Kuma, made the monitor by
hand, came back to Instellingen → Uptime, synced, and confirmed the match. Four screens for the
thing this module exists to do, and the first three in a different application. That is the
`pickers must offer inline-create` rule (docs/UX.md) one level up: **a picker that only offers
existing rows is unfinished for as long as the row you need is not one of them.**

The create form sits beside the attach control on both anchors and is shaped by four decisions.

- **The anchor is the route's record, never the form.** `uptimeCreateMonitor` reads
  `event.params.id`, exactly as `uptimeLink` does, and posts **no `company_id` at all** — the API
  derives it. `cloudflare` paid for the other shape once, in an adopt button that posted whatever
  was typed above it rather than the row it was drawn from.
- **The target is suggested, visible, and correctable.** A website has no URL column: its host is
  the apex or `www.` plus the apex depending on `websites.root`, which is what
  `matching.build_index` already derives when it decides which website a *found* monitor belongs
  to. The panel mirrors that rule off the record the host page has already loaded rather than
  fetching it — one more request on every website and domain page, to serve a form most visits
  never open, is the trade `docs/PERFORMANCE.md` bans — and puts the answer in a field you can
  see. A drift between the two derivations is then something you notice, not something you find
  out about later. Changing the type re-suggests the target **only while the box still holds the
  last suggestion**: once somebody has typed in it, that is the answer.
- **The settings are in the form, and blank means inherit.** Type, interval, retries and the
  profile, with every numeric box left out of the body when it is empty rather than sent as `0`.
  `None` means *follow the default* the whole way down (`profiles.resolve`), and an empty interval
  posted as a number would pin the monitor to the invariant floor — the kind of plausible wrong
  value nobody notices until a client asks why their site is checked every twenty seconds.
- **The group is where you finally use one.** #321 made groups manageable under Instellingen and
  gave them no point of use; the create form is it, and it offers only the **selected instance's**
  groups, because a monitor cannot sit in a folder on a different server.

Two things had to be fixed underneath it.

`create_monitor` wrote `company_id=payload.company_id` and took the three anchor ids on trust,
while `update_monitor`'s own docstring says the opposite in as many words — *"which client a
monitor belongs to is derived from what it was attached to, unless the caller said otherwise. Two
copies of 'whose monitor is this' is how the horizon starts disagreeing with the record."* A
monitor created from a website page therefore landed at `company_id IS NULL`: visible to staff
outside that client's group and invisible to the client whose site it watches (§15's #285/#266
rules) — the exact state #321 spent an issue removing from the sync path, back through the other
door, and this time for monitors we made ourselves. `_resolve_anchor` now runs the create payload
through the same resolution `link_monitor` uses: **one anchor, most specific first, the others
dropped**, resolved through `matching.anchor_query` so an id outside the caller's horizon is a
**404** rather than a silently-written link. Without it the create route was the one way past a
fence the other two write paths both stand behind.

And the picker needed a list it is allowed to read. `GET /uptime/instances` is gated whole on
`instance.manage`, which is deliberate — it reports credential state — but `uptime.monitor.write`
without `instance.manage` is a combination the permission split exists to make possible ("using
the connection somebody else configured", `permissions.py`). A form gated on a permission its own
route does not require is #310's mistake in miniature, so `GET /uptime/instances/selectable`
answers on `monitor.read` with `{id, name, mode, writable}` and nothing else: every field there
already rides every monitor row under `meta=true`, so it reveals nothing new, and no fact about a
credential leaves `instance.manage`. `writable` is computed server-side so the rule lives once — a
`linked` instance holds no credential by definition (§4), `_push` can never write to it, and a
monitor created against one would sit at `pending` for ever. The picker draws only writable
instances and says so in words when there are none, rather than offering a choice that can only
fail (#253's control that always refuses).

### The company panel had no renderer, so it printed its own JSON

`panels.py` has contributed `uptime.company` since gate 1 and the web module registered no
component for that key, so `companies/[id]` fell through to its `<pre>{JSON.stringify(...)}</pre>`
escape hatch and printed `{"total": 2, "by_status": {"active": 2}, "visible": true}` on every
client's page. Worth stating as a rule rather than a fix: **an API `PanelSpec` and a web
`companyPanels` entry are two halves of one panel**, and the fallback that makes the first
survivable on a developer's branch is what stops the omission being noticed on anyone else's.
`visible: false` renders nothing at all rather than "0 monitors" — a reader who may not look must
not be handed a number, and "none" is a different fact from "not for you".

## 8. Defaults are profiles, and the resolution is one clause

No agency is going to type `interval=60, retries=3, resend=30, accepted=[200-299],
expiryNotification=true, ignoreTls=false, notificationIDList=[2,5]` three hundred times.

`uptime_monitor_profiles(org_id, name, monitor_type, defaults JSONB, notification_ids, is_default,
active)` — *"Standaard website"*, *"Klant met SLA"*, *"Interne tooling"*.

**Three layers, and they must not fuse** (the argument `docs/REPORTING.md` makes about prompts, in a
smaller key):

1. **Product invariants** are code — an interval below what the instance will accept, a type the
   instance's version does not have.
2. **The tenant's editorial default** is a `uptime_monitor_profiles` row. A house policy compiled
   into Python is a decision we took for them.
3. **What is true about this one monitor** is the monitor row.

`NULL` means *inherit*, a value means *override*, and an explicit `null` posted is how *"volg de
standaard"* is expressed — §18's rule, already stated in CLAUDE.md for the marketing compare period.

**And the resolution is one clause**, taken by the create form, the bulk apply, the import, the sync
and the drift check alike — the #298 rule. Two copies of "what the default is" means the form writes
one thing, the drift check expects another, and every monitor in the tenant reads as `drift` forever.

Resolving to *no* profile falls back to the oldest active profile of that monitor type, and the first
profile of one **is** the default: nobody makes one profile and means "use none of it".

Built as `app/modules/uptime/profiles.py`, and two details are worth knowing. `PROFILE_KEYS` is
an **allow-list**, so a profile can never carry a `url` — a profile that can set a URL is a
profile that can point forty monitors at the wrong host in one save. And the invariants clamp
**last**, after both the profile and the monitor's own overrides, so nothing a tenant configures
can slip under a floor Uptime Kuma would refuse anyway.

The web half keeps the same rule: a profile's numeric boxes start **blank**, because blank means
inherit. Prefilling `60` would be a decision the tenant never made, and posting `0` for an empty
box would pin every following monitor to the invariant floor — a plausible wrong number nobody
notices until a client asks why their site is checked every twenty seconds.

### The checkbox trap, before it happens

This module is mostly booleans — `upside_down`, `ignore_tls`, `expiry_notification`, `active`,
`uptime_enabled`, `is_default`. **A checkbox posts its `value`, and an unticked one posts nothing**,
so use `checked()` / `triflag()` from `$lib/core/forms` and never compare against a literal. The
reporting module shipped `=== "on"` against a `FormCheckbox` that posts `"true"`; every checkbox in
the module silently posted `false`, it was invisible in review and invisible in use, and it took out
four unrelated settings. This module has more checkboxes than that one did.

## 9. Import is two different things

### Adopting an existing instance (Kuma → schakl)

Read monitors, groups, tags and notifications; match each monitor to a `website` or `domain` by
**normalised** hostname. §17's rule — *a pre-check must normalise the way the write does* — is what
stops the match finding nothing, deciding the row is a create, and then colliding on a name that was
already there.

The result is a **reconciliation screen**, not an action: matched / ambiguous / only-in-Kuma /
only-in-schakl. **An ambiguous match is refused, never guessed** (§17's `party` rule): two websites on
the same apex are an ordinary thing, and picking one attaches a client's monitoring to another
client's record with every row valid.

Adoption never writes to Kuma. Its output is our mirror plus a set of links a human confirmed.

#### How it actually works (#321)

The first release shipped the mirror and not the match, and the gap was invisible in the report
because `matched` / `ambiguous` / `unmatched` were on `UptimeSyncReport` from the start and nothing
ever set them: every sync answered `0 / 0 / 0`, which is exactly what "we looked and found nothing"
looks like. Meanwhile every adopted monitor sat at `company_id IS NULL` — **wrong on both sides of
the horizon at once**: the staff rule reads `NULL` as "not client data, stays visible" and
`__portal_horizon_clause__` reads it as "not yours", so a client's own monitor was visible to staff
outside that client's group and invisible to the client. Nothing on the screen said so.

- **`matching.py` holds the whole match**, and it is pure enough to test without a database. The
  host comes out of whatever the type stores (`url`, `hostname`, …) and is lowercased, de-ported,
  de-pathed, de-dotted; the ladder is **exact website host → every domain whose zone contains that
  host**, most specific first, and it stops at the first rung that answers. That second rung is
  what covers a client's mail server, VPN endpoint and NAS — hosts inside a zone we hold that will
  never be websites, and a third of what an agency actually monitors.
- **Ambiguity is an outcome, not a failure.** Two domain records that both contain
  `a.shop.klant.nl` are two defensible anchors, and picking the longest suffix would be this module
  deciding something it cannot know. Both come back; the screen asks.
- **The observation is stored, not just the verdict** — `uptime_monitors.link_candidates` +
  `link_checked_at`, `cloudflare`'s `observed_redirects` rule one module over. The timestamp is
  separate because an empty candidate list cannot tell *"we looked and there is nothing to link
  this to"* apart from *"nobody has ever looked"*, and the reconciliation screen has to say which.
  It also means the screen survives a page reload, which the alternative (recompute per render)
  does not.
- **`POST /monitors/{id}/link` is its own route, and it never dials out.** One anchor
  (`entity_type` + `entity_id`), not three ids that could contradict each other; an explicit null
  on both detaches (§18); the anchor is re-read through a horizon-filtered query at the moment it
  counts, so an anchor outside the caller's horizon is a 404 and a domain that changed hands since
  the sync cannot write yesterday's client onto today's monitor. `company_id` is **derived** there,
  in one place, or the record and the horizon start disagreeing again.
- **`POST /instances/{id}/links/apply`** confirms every proposal with exactly one candidate and
  reports the rest as `skipped`. It is the button an agency presses once after adopting an
  instance with two hundred monitors; it never resolves an ambiguity, because doing that in bulk
  is doing it two hundred times.
- **A link-only write no longer pushes.** `update_monitor` skips the whole Socket.IO round-trip
  when every changed field is one of ours (`website_id`, `domain_id`, `hosting_id`, `company_id`) —
  which is what `bulk.py`'s docstring already claimed, and what makes attaching forty freshly
  adopted monitors one request instead of forty logins.
- **Two statements for the whole instance**, whatever its size: one read of the unlinked monitors
  and one of the domains their hostnames could name.
  `test_the_match_is_one_query_however_many_monitors` counts the reads of `domains`, because a
  per-monitor lookup returns the identical report and is invisible in the JSON.

### Spreadsheets (§17) — **export-only, and the reason is not squeamishness**

The design said "importable". Building it changed that: every imported monitor row would be an
outbound Socket.IO round-trip — connect, authenticate, create, re-read — and the import path is
synchronous (`MAX_IMPORT_ROWS` is what keeps it honest until #77's background job lands). A
200-row file would hold one request open for minutes and spend the instance's login budget doing
it, which is the shape §3 exists to prevent. `importable=False` states it, exactly as `leave`
does for its own reason.

What an agency actually wants from a spreadsheet here is the **register**: which client sites are
watched, by which instance, which have drifted, and — the column that makes it a register rather
than a list — *whose* they are. That is a read, and it is the half that is useful today.

`websites` gets `uptime.monitors` and `uptime.drifted` as **contributed** columns, so it never
learns about monitors (§6), hydrated in one grouped query — a contributed column's getter looks
free, and that is exactly where an export goes N+1.

Note the two contracts that differ from what the design assumed: `ImpexExtension` takes
`module` / `write_permissions` / `apply` (not `contributor` / `read_permission`), and `filters`
may only name core's shared `FILTER_PARAMS`. So `instance_id` and `sync_status` became a
*column* rather than a filter — which is what a register is read for anyway.

### Bulk carries only what does not push (§18)

`company` is editable, because attaching a freshly-adopted instance's monitors to their clients
is precisely the chore a selection exists for and it touches nothing at Uptime Kuma.
`interval_seconds`, `target`, `name` and `profile_id` are **absent**: each means a socket
round-trip per row, so a forty-row edit would hold one request open for a minute. `target` is
excluded for a second reason as well, the one `Domain.name` is excluded for — a shared value that
retargets forty monitors at one host loses monitoring silently, with every row valid.

Bulk delete is **local only**. Core's contract has no way to carry "and also at the far end",
and that is the right answer rather than a limitation to work around: a selection is not the
place to take an irreversible action on a client's live monitoring.

## 10. The company horizon needs an event that does not exist yet

A monitor's client comes from website → domain → company, or from hosting, or from nowhere at all
(an agency also monitors a client's mail server, VPN endpoint and NAS, none of which are websites).
That is #285's failure mode 1 — **no anchor** — and it has two possible answers.

**We denormalise `company_id` onto the monitor**, rather than declaring
`__company_horizon_clause__` the way `cloudflare`'s rows do. The clause is right when every row hangs
off a domain; here a third of them will hang off nothing, and a three-way clause with a null branch
is the shape that filters nothing at all when it is wrong. A `NULL` `company_id` means *attached to
no client*, which #285 already says stays visible to restricted staff.

The cost is that it must stay in step, and **nothing currently announces a domain changing hands**.
So `domain.company_changed` belongs on `app/core/events.py`, emitted by `domains` and subscribed
here — the §6 in-process bus, handled in the emitter's transaction, so the move and the
recomputation commit together or neither does.

**This is the one piece of the design that is not built.** It was scoped into gate 1 and did not
land, because it is a change to another module and every gate had a way to be useful without it.
That leaves a real, bounded gap: a domain moving from client A to client B leaves its monitors
readable by A's staff and invisible to B's until somebody corrects the link by hand. Bulk edit of
`company` (§9) is the manual repair, which is part of why that column is editable — but the
event is the fix, and nothing on screen currently says the horizon is stale.

That is a change outside this module's blast radius and it is still the right call, because the
alternative is a **horizon leak with a known window**. A domain moving from client A to client B
leaves every monitor beneath it readable by A's staff and invisible to B's until the next reconcile.
Everything else in this document can be deferred to a later gate; a stale horizon cannot, because
nothing on the screen would say it was stale. `cloudflare` wants the same event and today works
around its absence with a clause, so this is a small shared addition rather than a cost this module
invents.

The semantics are that **a monitor follows its domain**. It is monitoring that domain's website; if
the website is now B's, the monitoring is B's, and no other reading makes sense. The nightly reconcile
recomputes as a backstop, and a test asserts the event actually fires — an event nobody emits is
indistinguishable from one nobody subscribed to, and both fail silently.

## 11. Alerts come back in, and a webhook body is a hint

Uptime Kuma has ninety-odd notification providers and is better at delivery than we will be. We do
not compete with that. What an agency wants is for a down site to become **a thing on the client's
record** — a notification, a timeline entry, optionally a task — and the cheapest correct way to get
it is for Kuma to POST at us.

`POST /uptime/hook/{org_id}.{instance_id}.{secret}` — the Google channel-token shape already in
`app/core/payments/tokens.py`, reused rather than reinvented. `no_permission_required`, because the
caller is a monitoring server holding no session, and five gates in this order:

1. the token names the tenant,
2. RLS is bound before anything is read,
3. the secret is compared in constant time — **a mismatch is a bare 404**, never a 401 that would
   confirm the instance exists,
4. the body is read for the monitor id and the claimed state **and nothing else**,
5. on a `managed` instance the state is confirmed by an authenticated re-fetch.

**A webhook body is a hint, never a fact** (`docs/PAYMENTS.md`): Kuma's webhook is unsigned JSON
that anyone who learns the URL can post. On a `linked` instance there is no re-fetch available, which
is the honest limit of that mode — the event is recorded as *reported by the instance*, and the UI
says so rather than presenting it as observed.

Three things this route may not do, each of which a "helpful" version of it would:

- **It never creates.** A monitor id we do not already hold is a 404, not an insert. A route that
  auto-registers what it is told about is an unauthenticated writer of tenant rows, and the fact that
  the token was right only proves the *instance* is known — not that this monitor is.
- **It never writes configuration.** Its entire write surface is one heartbeat row and one
  notification event. Nothing about a monitor's own definition is reachable from here, so the worst a
  leaked URL buys is a false status on a known monitor — recoverable, and contradicted by the next
  poll on a `managed` instance.
- **It is bounded before it is parsed.** A body-size cap and a per-token rate limit, both checked
  before the JSON is decoded — §17's rule that every cap is checked before the work it bounds. An
  ingest route that is cheap to call and expensive to serve is the one shape a public URL must never
  have.

Ingest is **idempotent at the database**, not in application code. A monitor flapping delivers the
same transition twice, and "have we recorded this yet?" followed by an insert leaves a window every
retry enters — the lesson `invoice_payments` already learned. A partial unique index on
`(org_id, monitor_id, state, observed_at)` makes the duplicate impossible rather than unlikely,
including across two API replicas that share no memory.

Downstream, it is the existing machinery and no new channel: a `NotificationWatcher` so the right
people are told through `docs/NOTIFICATIONS.md`, and an `AutomationActionSpec` (#27) so a tenant can
wire *"down longer than five minutes → taak op de klant"* without us guessing which of those they
wanted.

**The ingest route is `license_exempt`.** An expired licence makes a module read-only; it does not
make a client's outage stop having happened. Gate what the agency *does* — creating monitors, editing
profiles, pushing changes — never the recording of what has already happened to them. This is the
narrow precedent `docs/PAYMENTS.md` set for the Mollie callback, applied to the one other place where
refusing the write loses information no retry recovers.

## 12. The activity trail (§16)

`UptimeInstance` and `UptimeMonitor` both carry `AuditableMixin`, and both detail views render the
trail. Rotating a credential that can rewrite a client's monitoring, and repointing a monitor at a
different URL, are exactly the changes an agency needs to be able to attribute six months later.

Three rules, all of them §16's:

- **The credential is never in the trail** — only the fact that it changed (`token_enrolled`,
  `token_reauthenticated`, `headers_changed`). The same rule `cloudflare_accounts` follows.
- **`payload` for an edit is `{changes: {field: {from, to}}}` over the record's own definition
  fields** — our decided columns. `remote_snapshot`, `last_observed_at` and `sync_status` are not
  edits, they are observations, and a trail that logged every sync would bury the one line somebody
  is looking for under a thousand.
- **A sync writes no trail entries at all.** What a reconcile *applies* on the tenant's instruction
  does, attributed to whoever pressed the button — including the impersonator, if there was one.

The one addition this module makes to the pattern: **the webhook's ingest is not an activity
entry.** It is a heartbeat and a notification event. An unauthenticated caller must never be able to
write lines into an audit trail, which is the shape that turns a leaked URL into a way to bury
evidence under noise.

## 13. Permissions (§15)

| key | scopes | covers |
|---|---|---|
| `uptime.instance.manage` | — | add / edit / verify / delete an instance, rotate its credential, run a sync |
| `uptime.monitor.read` | `own`, `any` | the monitor list, a monitor's detail, heartbeats, the status panels |
| `uptime.monitor.write` | — | create, edit, delete a monitor or group; adopt an import |
| `uptime.monitor.pause` | — | pause / resume, without the ability to change what is monitored |
| `uptime.profile.manage` | — | the default-settings profiles |

`instance.manage` is **admin-only by default**: the credential it holds can rewrite a client's entire
monitoring, and minting a credential is a different act from using it. `monitor.pause` is separate
from `monitor.write` because silencing an alert during a planned migration is an ordinary thing to
ask of an ordinary employee, and repointing a monitor is not.

`monitor.read` is scoped because scope is the only thing that can fence it (#266): `:own` is what a
client-portal login holds, `:any` what staff hold, and the company horizon still decides *whose*.
The monitor model declares `__portal_horizon_clause__` — the client's own companies, never internal,
never a monitor attached to no client — because `GET /files` and `entity_visible` are the gates that
would otherwise answer with the staff rule.

Nothing here is granted to `client` beyond `uptime.monitor.read:own`. Instances, profiles and the
sync are staff surfaces with no client of their own.

## 14. Errors

`message` in the error envelope is always an i18n key (§9), so Kuma's own text never goes in it — it
is not translatable. Where the operation still commits (verify, sync, adopt) the text is persisted to
the row's `last_error` and rendered by the settings screen and the panel.

The distinctions that must survive into separate keys, because each sends an admin somewhere
different. The first four are reachable at enrolment only; the rest at any time.

| condition | key | why it is its own key |
|---|---|---|
| wrong username or password | `errors.uptime_credentials_rejected` | enrolment only — nothing later holds a password |
| 2FA required, none supplied | `errors.uptime_totp_required` | actionable; "wrong password" is not |
| TOTP rejected | `errors.uptime_totp_rejected` | usually clock skew, never the password |
| rate limiter tripped | `errors.uptime_rate_limited` | retry later; rotating the credential makes it worse |
| socket will not open | `errors.uptime_unreachable` | the tunnel, the hostname, or Kuma is down — not the credential |
| Access / proxy refused the handshake | `errors.uptime_gateway_refused` | a service-token problem reads as "Kuma is down" otherwise |
| cached token no longer verifies | `errors.uptime_reauth_required` | the Kuma password changed or the user was deactivated — re-enrol, do not retry |
| target is not Uptime Kuma | `errors.uptime_not_kuma` | §5's proof-of-identity gate; a typo'd hostname, not an outage |
| private target refused on cloud | `errors.uptime_private_target` | says *why* it is refused here and would work self-hosted |
| version below the module's floor | `errors.uptime_version_unsupported` | carries the observed `server_version` |
| monitor type unknown to this instance | `errors.uptime_type_unsupported` | eleven of the 33 are version-dependent |

`errors.uptime_reauth_required` is the one that must not be collapsed into
`errors.uptime_credentials_rejected`. They are the same HTTP shape and opposite instructions: one
means the credential you just typed is wrong, the other means the credential you typed months ago
was correct and has since been revoked at the far end. Kuma distinguishes them for us — the token
path answers *"The token is invalid due to password change or old token"* — so collapsing them
would be discarding information the server volunteered.

## 15. Testing

`tests/uptime_fake.py` is a stateful stand-in installed through the client's single transport seam.
It holds monitors, groups, tags and notifications, and it must model the things that actually break:

- **A credential it rejects, it rejects everywhere.** The `cloudflare` lesson: a fake that
  authenticates one call and refuses another is a fake in which the one-way error flag does not
  exist, and the only test that could catch it passes against a server that is not the one we call.
- **A version.** `info()` answers a configurable `server_version`, so the version floor, the
  type-unsupported error and the shim are all reachable from a test.
- **Recovery.** A `revoked` fixture that can be flipped back, because the drift and error paths are
  only half-tested until something clears them.
- **Drift.** A test sets up "somebody changed this in Kuma" by writing into the fake's monitor
  directly and asserts on what the module *reports*, never on what it silently overwrote.
- **A slow instance.** The timeout path is a real branch and a screen depends on it failing fast.
- **A token that stops verifying.** The fake answers `loginByToken` with Kuma's own password-change
  refusal, so `needs_reauth` (§4) is reachable — and so is the assertion that the mirror still
  renders while the instance is in it.
- **A secret that changed.** The fake mutates `basic_auth_pass` and the test asserts
  `drift (credential)` from the fingerprint comparison (§4) *and* that no plaintext reached the row.

Nothing in the suite touches the network; a test that forgot the fake fails loudly on connect.

Five safety assertions do not belong to the fake, because they must hold before a connection is ever
attempted, and each of them is a rule §5 and §11 would otherwise only state:

1. `base_url` at a private address is **refused on cloud and accepted on self-host** — parameterised
   over `deployment`, because a single-posture test proves the half that was never in doubt.
2. A handshake that 302s off-host is refused, not followed.
3. A target that opens a socket but never answers `info()` never reaches `active`.
4. `last_error` never contains a response body — asserted by pointing the fake at something that
   answers with a recognisable secret string and grepping the stored row for it.
5. The webhook: an unknown monitor id is a 404 and **writes nothing**, a wrong secret is a bare 404
   in constant time, an oversized body is refused before it is parsed, and a replayed delivery
   inserts one heartbeat rather than two.

Beyond that, the ordinary gates: a tenant-isolation test per table, the deny-by-default sweep and the
company-group sweep pick this module up for free, a test asserts `domain.company_changed` actually
fires (§10), and **a `count_queries` budget test lands with the feature** — a company panel that folds
every monitor's last heartbeat is exactly the endpoint that is one grouped query at three monitors and
one-per-row at three hundred (`docs/PERFORMANCE.md`).

## 16. What is not here

- **Status pages, maintenance windows, proxies, docker hosts and remote browsers.** All reachable
  over the same socket, none of them the thing an agency asked for. Maintenance windows are the most
  likely second slice, because "do not alarm the client during a planned migration" is a real ask and
  it pairs with `uptime.monitor.pause`.
- **Kuma's own notification providers.** We read the id list so a monitor can be assigned to them; we
  do not manage them. An agency configuring Slack in Kuma is doing the right thing.
- **Heartbeat history as a data warehouse.** `uptime_heartbeats` is a bounded rolling window — what a
  panel and a report section draw — pruned nightly. Kuma keeps the real history and answers questions
  about it better than a mirror would.
- **`domain.company_changed`** (§10) — the one designed piece that did not land, and the only
  entry on this list that is a *gap* rather than a decision.
- **Importing monitors from a spreadsheet** (§9), until #77's background job exists.
- **A monitor create/edit screen.** The API is complete (`POST`/`PATCH /uptime/monitors`), and
  the web surface today is the settings screen, the drift queue and the website panel. An agency
  adopts an instance and reconciles; creating monitors one at a time in schakl rather than in
  Kuma's own UI is the less pressing half.
- **Provisioning a tunnel** (§5). Four documented steps in somebody else's Cloudflare account.
- **A pinned certificate fingerprint**, the better answer to a self-signed instance than
  `ssl_verify = false` (§5). Worth doing; not worth blocking the first gate on.
- **An SLA report section.** It is the obvious next thing once `reporting` (#300) and this module are
  both in: a `ReportSectionSpec` contributing uptime percentage and incident count to the monthly
  client report, with the numbers frozen into `reports.data_snapshot` like every other section's.
