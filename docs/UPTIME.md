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

Run against `louislam/uptime-kuma:2.5.0`. Nine findings, of which **four contradict something
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
group is a row in `uptime_monitors` with `type = "group"` and `parent_group_id` a self-FK, resolved
from `parent` at import and back to it on write.

The obvious agency shape — **one group per company**, every one of that client's monitors beneath it
— is a convention this module *offers* at import and on create. It is not a schema fact. A tenant who
already groups by environment, or by datacentre, or not at all, must not have their tree rewritten by
an import, and no code may assume a monitor's group tells you its client (§10 is what tells you).

Two consequences worth knowing before writing the sync: a group's own `active` cascades to its
children in Kuma, so pausing a group pauses monitors whose own row still reads active; and deleting a
group is a question Kuma answers on its own terms, so this module refuses to delete a group that
still has children rather than discovering the answer on a client's monitoring.

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

### Spreadsheets (§17)

An `ImpexDescriptor` for `uptime_monitor`, and an `ImpexExtension` contributing `uptime_*` columns to
the **websites** entity — the panels pattern applied to impex, so a website import can carry
*"monitoring: ja, profiel: Standaard"* without `websites` importing our internals. Contributed
columns are never `required`, and `apply` runs in the import's own transaction and never on a dry
run.

A `BulkDescriptor` (§18) covers the selection case: forty websites → apply a profile, pause, resume,
move to a group. `editable` is an allow-list, and `url` stays out of it for the reason `Domain.name`
does — a bulk edit that can retarget forty monitors at one URL is a way to lose monitoring silently.

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
So gate 1 adds `domain.company_changed` to `app/core/events.py`, emitted by `domains` and subscribed
here — the §6 in-process bus, handled in the emitter's transaction, so the move and the recomputation
commit together or neither does.

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
- **Provisioning a tunnel** (§5). Four documented steps in somebody else's Cloudflare account.
- **A pinned certificate fingerprint**, the better answer to a self-signed instance than
  `ssl_verify = false` (§5). Worth doing; not worth blocking the first gate on.
- **An SLA report section.** It is the obvious next thing once `reporting` (#300) and this module are
  both in: a `ReportSectionSpec` contributing uptime percentage and incident count to the monthly
  client report, with the numbers frozen into `reports.data_snapshot` like every other section's.
