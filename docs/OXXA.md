# OXXA registrar integration

> The `oxxa` module (issue #296, the registrar half of epic #278): the four facts only a
> registrar knows about a domain, and the one write that moves a delegation. Business-licensed
> (`sku="oxxa"`). Read this before changing anything under `apps/api/app/modules/oxxa/`,
> `apps/api/app/core/registrar/`, `apps/web/src/lib/modules/oxxa/` or
> `apps/web/src/routes/(app)/settings/oxxa/`.

Sibling to `docs/CLOUDFLARE.md`, and the two halves only meet at one seam — §7. `cloudflare`
owns the zone; this owns the delegation that points at it. Neither imports the other (CLAUDE.md
§6), and the only place that sees both is the web layer, one screen up (§7).

## 1. What it is for, and what has never been run

`domains` (#90) already stores what public DNS answers. It cannot know **when a domain expires**,
**whether it is locked against transfer**, **which nameservers the registry has delegated** or
**who the registrant is**, because none of that is in DNS. This module asks OXXA, stores the
answers next to what we asked for, and adds one write: `domain_ns_upd`, so pointing a domain at a
Cloudflare zone no longer means logging into the OXXA portal by hand. That write is what #95
never got to build and what #278 deliberately left open.

**Nothing in this module has ever been exercised against a live OXXA account.** It is written
from OXXA's official API documentation, v1.2 — a real document, not memory, which is the
distinction CLAUDE.md §11's ban actually draws. But the issue's own "blocked by" asked for
credentials *and* documentation, and only the documentation exists. The document itself says its
examples are illustrative and that an implementation must be based on the real response, so every
field here is read defensively: a missing tag is `None`, `None` means *not reported* and never
`False`, and nothing assumes a shape it has not been shown.

### The first-credential checklist

Run these in order the day a credential arrives. Each one is a place the code committed to a
reading of the document that the document itself declines to guarantee.

1. **The envelope.** Confirm `<channel><order>…</order></channel>` with `status_code`,
   `status_description`, `price`, `details`, `order_complete`, `done` — and confirm `<details>` is
   the container for everything payload-shaped. `_parse` falls back to `.//order`, but if the real
   envelope nests differently the fallback is silently finding the wrong node.
2. **`status_code` spacing.** Capture real samples across several commands. The document shows
   both `XMLOK 16` and `XMLOK18`; `_STATUS_RE` normalises both plus any casing. A third shape
   (`XML-OK`, a trailing period, a leading BOM) would make every call read as an error.
3. **Both date formats, from the two commands that differ.** `domain_list` is documented as
   `2009-10-06` and `domain_inf` as `04-10-2009 (dd-mm-yyyy)` — with the format hint *inside the
   value*. `parse_date` strips parentheses and tries four patterns. Verify that `domain_inf`
   really is day-first: a `03-04-2027` parsed the wrong way round misprices a renewal by nine
   months and nothing in the response says so.
4. **The ISO-8859-1 decoding.** Fetch a registrant with a real Dutch name — a diaeresis, an
   accent, `'s-Hertogenbosch` — through `identity_get`, and check what lands in
   `OxxaDomain.registrant`. We parse **bytes** so the XML prologue decides; the failure to look
   for is a prologue that declares ISO-8859-1 while the body is actually UTF-8 (or the reverse),
   which yields mojibake rather than an error.
5. **`nsgroup_add`'s return.** The handle is documented to come back in `<details>` as bare text.
   Confirm it, and confirm whether it is bare text or a child tag — `_create_nsgroup` reads
   `detail_text()` and falls back to re-reading by alias, so a child tag works but costs an extra
   round trip on every group creation, which is worth knowing before it becomes normal.
6. **`nsgroup_list`'s filter and shape.** Does `alias=` actually filter, or does it return
   everything? Are the children `<nsgroup>` with `<name>` and `<handle>`? The whole find-or-create
   rule (§4) rests on being able to look a group up by name.
7. **Whether an auth failure is distinguishable.** Send a wrong password and record the exact
   `status_code` and `status_description`. OXXA documents no distinct status code for
   authentication — every failure is `XMLERR` — so `_AUTH_MARKERS` sniffs OXXA's prose for
   `login` / `wachtwoord` / `authenticat` / …. If a real code exists, replace the prose sniff with
   it; if the real wording matches none of the markers, a wrong password currently reports as a
   generic registrar error and the admin is told to retry something that cannot work.
8. **`domain_ns_upd` on success.** Its documented *success* example carries
   `<order_complete>FALSE</order_complete>`. Confirm that success really is `XMLOK`/`XMLPEN` and
   that `order_complete` means something else entirely — the code treats only the prefix as
   load-bearing, and if that reading is wrong every push reports success while nothing moves.
9. **`domain_inf` for a domain the account does not hold.** `get_domain` swallows exactly one
   business error — an `XMLERR` here becomes `None`, "we do not hold it", which is a fact and not
   a failure. Verify the registrar does not use the same `XMLERR` for "your credential expired".
10. **`user_tld_list`'s shape.** One element *per TLD, named after the TLD* (`<abogado/>`). This
    is the authority for the `sld`/`tld` split; if it is a flat list of values instead, `suffixes`
    returns tag names that are not TLDs and every split refuses.

Until at least 1–3 and 7 are confirmed, treat a green settings screen as evidence that the
credential is right and nothing more.

## 2. Auth, and the query-string hazard

OXXA authenticates with `apiuser` and `apipassword` **GET parameters**. There is no header form.
TLS covers the wire; it covers nothing else, and this is the single biggest difference between
this client and the Cloudflare one.

- **`client.redact` is the one function standing between OXXA's auth design and the activity
  log.** It blanks `apipassword=…` in any string, and it is applied to anything derived from a
  request before it is logged, stored on `last_error`, or raised.
- **No httpx exception is ever formatted into a message.** `str()` on an httpx error embeds the
  full request URL, password and all. The transport handler logs `type(exc).__name__` and
  `_safe_url(command)` — the endpoint plus the command name, nothing else — and raises an
  `OxxaError` carrying our own sentence.
- **The invariant, stated exactly**: no `OxxaError` message is ever *derived from a request*, and
  any provider text passes through `redact` before it is raised or stored. What such a message
  holds is one of two things, and it is worth knowing which, because both land in the same
  `last_error` column. Most of them are the client's **own English sentences**, written in
  `client.py` and untranslatable by design — the transport failure, an HTTP status, an over-size
  or unparseable or DTD-bearing response, an envelope with no `<order>`, a nameserver count
  outside 2–6, a missing group handle, the nsgroup conflict. Exactly one path — the business
  failure at the end of `_parse` — carries **OXXA's** `status_description`, redacted. So a
  `last_error` reading Dutch registrar prose and one reading flat English are both working as
  intended; neither is a leak, and neither is translatable.
- **The standing hazard: `httpx` logs `str(request.url)` itself, at `INFO`.** Nothing in this
  module can prevent that. Raising the `httpx` logger to `INFO` — or setting a root handler to
  `INFO`/`DEBUG` while debugging something unrelated — writes reseller passwords into the
  container log, and container logs are shipped, retained and read by people who are not the
  tenant. If httpx request logging is ever wanted, add a filter that applies `redact` to the
  record, and do it in the same change.
- The credential is Fernet at rest (`app.core.crypto`, the `*_encrypted` convention from
  `docs/GOOGLE.md`), write-only through the API: `OxxaAccountRead` carries `password_configured`
  and never the value. Rotating it clears everything the old credential vouched for — the TLD
  list, the balance, `last_verified_at` — so a stale "verified" badge cannot speak for a password
  nobody has tested. The activity trail records *that* it changed, never the value.
- A `SCHAKL_ENCRYPTION_KEY` rotation leaves an unreadable secret. That is
  `errors.oxxa_credential_unreadable` (409), not a 500: the fix is re-entering the password, not
  retrying.

### MD5 is not an improvement

OXXA accepts the literal string `MD5` followed by the md5 hex of the password in place of the
password. This module does not use it, and adding it would not make anything safer:

- the digest **is** the credential. Anyone who reads it out of a proxy log, an httpx `INFO` line
  or OXXA's own access log authenticates with it exactly as they would with the plaintext. It
  changes what the secret looks like, not what an interceptor needs.
- it is unsalted md5, so for any password short enough to be typed by a human it is also the
  plaintext, with one lookup.
- it is a *second credential shape* to store, rotate, redact and get wrong, in exchange for
  nothing.

Written down here so nobody adds it as a security hardening later.

## 3. Rate limits, cost, and why sync is a button

The one published quota in the document is on **`domain_check`: 9 999 calls per day**. This
module never calls it — there is no availability search here — but a domain-availability screen is
the obvious next thing somebody adds, and it is the command with a cap.

**Some commands are billed per call.** The envelope has a `<price>` field at all because orders
cost money; `autorenew`'s own documented example carries one. That is why `OxxaResponse.price` is
parsed and why `_RETRYABLE_COMMANDS` is an **allowlist of reads**: OXXA documents no idempotency
key, so replaying anything that mutates or is billed replays as a second order. `nsgroup_add` is
pointedly not in it — OXXA creates a *second group* rather than failing, and a duplicate is worse
than an error.

Every command this module actually uses is a 0.00 read except the two writes. (`dnssec_info` sits
in `_RETRYABLE_COMMANDS` unused — it is the read a future DNSSEC panel would make; today DNSSEC
comes from `domain_inf`.)

| command | used by | cost |
|---|---|---|
| `funds_get` | verify | 0.00 |
| `user_tld_list` | verify (caches the suffix list) | 0.00 |
| `domain_list` (`records=-1`) | sync | 0.00 |
| `domain_inf` | per-domain refresh, and the pre-check inside a push | 0.00 |
| `identity_get` | per-domain refresh only | 0.00 |
| `nsgroup_list` / `nsgroup_get` | find-or-create, sync's group resolution | 0.00 |
| `nsgroup_add` | push, only when no matching group exists | write |
| `domain_ns_upd` | push, only when the delegation actually changes | write |

**A whole register sync is one `domain_list` call** plus one `nsgroup_get` per *distinct*
nameserver group — a reseller account with 400 domains usually has a handful. `domain_list`
carries expiry, lock, autorenew, the contact handles and the group reference; the only things it
omits are DNSSEC and the registrant's name, and both are the per-domain refresh's business. That
asymmetry is the whole design: the cheap thing is register-wide, the expensive thing is one domain
at a time.

**And it is a button, not a cron**, for three reasons. A register changes slowly — an expiry date
moves once a year. Scheduling recurring traffic against an API this repository has never
successfully called once is the wrong first move. And a cron that writes rows nobody asked for
turns a parsing bug (§1) into a silent data problem across the whole register rather than a
visible failure on the screen of the person who pressed the button. When it does become a cron it
should be `run_per_org` (CLAUDE.md §6), jittered, and `domain_list`-shaped — never the per-domain
path.

**The bound on a sync is structural, not a constant.** `OxxaService.sync_account` makes one
`domain_list` call and then resolves *nameserver groups only*: `{d.nameserver_ref for d in found}`
is de-duplicated before a single `nsgroup_get` is issued, so the request count is one plus the
number of distinct groups, whatever the register's size. It **never calls `identity_get`** —
that is one request per handle, and an unbounded read is a build break (§9). The registrant's
name is `OxxaService.refresh_domain`'s business: one domain, one handle, and only because a
human pressed the button. Anything added to the sync path has to keep that shape.

## 4. Nameservers are a shared group, and that is the rule

**The single most dangerous thing in this integration.** OXXA has no per-domain nameserver list.
`domain_ns_upd` takes an `nsgroup` **handle**, and a group is a shared object whose documentation
says in as many words that updating it *"wordt doorgevoerd op alle domeinen die gebruik maken van
het profiel"* — every domain pointing at that group moves with it.

So `OxxaClient.set_nameservers` **finds or creates** a group and never, under any circumstance,
calls `nsgroup_upd`. Concretely:

- **The alias is derived from the contents.** `nsgroup_alias` sorts and de-duplicates the
  normalised hostnames, hashes them, and returns `schakl-<sha256[:12]>`. Deterministic on the
  *set*, so two agents racing — or one retry after a timeout — converge on the same group instead
  of littering the account with duplicates. The `schakl-` prefix is how anything in this
  integration can answer "did we create this?".
- **Lookup is by alias, never by membership.** Enumerating members costs one `nsgroup_get` per
  group and a reseller account holds many; ours are findable by name precisely because the name is
  a function of the members.
- **A group with our name holding different nameservers is refused, not reused.** Somebody edited
  it by hand at OXXA. Reusing it would repoint every other domain in it; rewriting it is the
  forbidden call. So `set_nameservers` raises **`OxxaConflictError`**, which `service._translate`
  maps to `errors.oxxa_nsgroup_conflict` — and on the push path that key comes back as the
  `error` of a 200 `ok:false` result (§10), with the row left at `ns_push_status="error"`. The
  dedicated class exists because the generic shape lied: a bare `OxxaError` carries neither an
  HTTP status nor an OXXA status code, which `_translate` reads as `oxxa_unreachable` — *"try
  again in a moment"*, in front of the one operation in this module that must never be repointed
  blind, for a state no retry can ever fix. `docs/CLOUDFLARE.md` §5's rule generalises: conflicts
  are reported, never resolved.
- **A domain already pointing at the right handle produces no write at all.** Once the group is
  in hand, `set_nameservers` reads `domain_inf` and, if the domain already carries that handle,
  skips `domain_ns_upd` entirely and returns the handle. This is what makes the call idempotent,
  which is what makes it retryable, which is what the whole hand-off in §7 rests on — and it is a
  requirement of the `RegistrarProvider` protocol, not an implementation detail of this one.
  Note that `NameserverPushResult.changed` is a **different** question, answered one layer up:
  the service compares the row's belief *before* the call (`nsgroup_ref`, `ns_observed`) against
  what was pushed. A row we have never synced holds no belief, so it reads `changed=true` even
  when nothing moved at OXXA — the honest default when we cannot know, and the reason `changed`
  is a report about our record rather than a claim about the registry.
- **2–6 members**, twice: `NameserverPush` refuses the payload before the service does anything,
  and `set_nameservers` checks again before any call is made (OXXA's documented group size), so
  neither a form nor a future internal caller can get past it. A bare label with no dot is refused
  in the schema too: OXXA would accept it and break the delegation.

Two consequences worth stating plainly. **Groups accumulate and are never deleted** — deleting is
the dangerous direction, and a handful of inspectable `schakl-…` groups is cheap. And because
Cloudflare assigns a *different* nameserver pair per zone, expect roughly one `schakl-` group per
connected domain. That is the shape of OXXA's API, not a leak.

## 5. Decided, observed, and what the world sees

A domain now has **three** different nameserver facts, and conflating any two of them is a bug:

| where | what it means | written by |
|---|---|---|
| `Domain.nameservers` | what **public DNS** answers right now | the domains module's own lookup (#92) — its cron, and its explicit refresh |
| `OxxaDomain.ns_observed` | what the **registry** has delegated | this module, on sync/refresh/push |
| `OxxaDomain.ns_desired` | what **we asked for** | this module, on push only |

They disagree for entirely ordinary reasons — a delegation change takes hours to propagate — so a
reconcile *reports* the disagreement and never resolves it (CLAUDE.md §10). `ns_desired` is
`NULL` for almost every row forever, quite correctly: most domains are delegated somewhere we
never touched, and "we never pushed" is not drift.

**The trap:** the domains module stores `[]` for a *failed* public lookup, indistinguishable from
"no nameservers". So `_domain_issues` compares registry against public **only when both are
non-empty**. Reading that column as evidence would light up `not_delegated_yet` for every domain
whose resolver was briefly unreachable. Any new comparison against `Domain.nameservers` must carry
the same guard.

`ns_push_status` has five values rather than a boolean because each one needs a different button:
`pending` (never pushed), `active` (registrar agrees with us), `drift` (registrar holds something
else), `missing` (registrar holds nothing) and `error` (the push was refused, and `ns_desired` +
`last_error` are kept so a retry does not make the user retype anything).

A domain that leaves the register — transferred away, expired — keeps its row and gets
`registry_status = "gone"`. Deleting it would take the record of what we pushed with it. And a
register row that matches no schakl domain keeps `domain_id = NULL` and is listed, never hidden:
`GET /oxxa/domains?linked=false` is the most valuable thing a sync produces, because those are
domains the agency is paying to renew and quite possibly not billing anyone for.

## 6. A synced registrant is reported, not applied

**This is where the code and the issue text differ, deliberately.** #296 says
`Domain.registry_contact_party_type/id` "is where a synced registrant lands". It does not land
there. The registrant is snapshotted onto `OxxaDomain.registrant` and **shown**, beside the
domain's own record, on the panel and in the register list. Applying it stays a human act.

Note what that does *not* include today: nothing compares the two. There is no
`registrant_mismatch` issue key, and `_domain_issues` never reads either field — a human looks at
the panel and sees both. Comparing them mechanically means deciding when `"Jansen Beheer B.V."`
and a party row are the same legal person, which is the same guess as applying it, one step
earlier. (The two `registry_contact_*` columns appear in this module's bare-table declaration of
`domains` and are never selected. Harmless, and a hook if a later issue does want the comparison.)

Three reasons, and they compound:

- **`registry_contact_*` is a decision a user made.** It is a party reference (`PartyRef` — an
  agency, a company, an employee, a contact) that somebody chose. What the registrar returns is a
  handle and some strings. Turning `"Jansen Beheer B.V."` into a party row is a guess, and
  silently replacing a user's choice with a guess from WHOIS is the single most surprising thing
  this module could do.
- **The handle is shared.** One `identity` sits behind dozens or hundreds of domains at a
  reseller. A wrong mapping does not misfile one domain, it misfiles the whole cohort.
- **It would be a write into another module's table**, which §6 forbids for good reason:
  `domains` owns its own validation, events and activity trail, and an import that bypasses them
  is a backdoor around the service layer (§17's rule, one layer out).

What exists instead: the snapshot (`ref`, name, organisation, e-mail, city, country) resolved on
the explicit per-domain refresh, snapshotted rather than joined live for the reason §16 snapshots
an actor — an answer that evaporates when the handle is deleted is not an answer. The obvious
follow-up is an explicit "apply this registrant to the domain record" action with a party picker
in front of it. That is a UI decision with a human in it, which is the whole point.

## 7. The Cloudflare hand-off

The flow, end to end:

1. `POST /api/v1/cloudflare/domains/{id}/connect` — adopts the domain's existing Cloudflare zone
   or creates one, stores `CloudflareZone.name_servers`, records `cloudflare.zone_created` or
   `cloudflare.zone_connected` with the nameservers in the payload.
2. `POST /api/v1/oxxa/domains/{id}/nameservers` with exactly those nameservers (plus `account_id`
   if the tenant has more than one active register) — finds or creates the group, pushes
   `domain_ns_upd`, records `oxxa.nameservers_pushed` on the **domain**, which is the record a
   user opens (§16).
3. Propagation. The registry moves in minutes to hours; public DNS follows its TTL. Until the
   domains module's own lookup catches up, `not_delegated_yet` is the expected state, not a
   failure.

**The two calls are deliberately separate**, and all three reasons point the same way:

- They cannot share a transaction. `ctx.release_db()` commits on entry, so an external call made
  inside a request is never rolled back by a later failure. "Both or neither" is not on offer at
  any price.
- CLAUDE.md §6 forbids `oxxa` importing `cloudflare`'s internals to make the first call itself.
- #278 asked for the failure path to be explicit and the push to be **retryable per domain**
  rather than a support ticket.

**Where the composition lives: the web layer, and it is built.** `OxxaPanel.svelte` renders at
position 40 on the domain detail page, directly under the Cloudflare panel at 30 — the delegation
follows the zone, and reading them in that order is what makes the push step legible. Its
nameserver box opens **pre-filled with Cloudflare's pair**, read out of the page's own panel data
(`panels[key="cloudflare.domain"] → data.status.expected_nameservers`) *structurally*, without
importing that module's types: §6 forbids the import, an absent panel must read as "no
suggestion" rather than a broken page, and it costs no extra API call because the page is holding
that data either way. A "Nameservers van Cloudflare gebruiken" button puts the pair back after an
edit or a failed attempt — and it is absent while the box already holds that pair, because a
control that would write what is already there does nothing (#253).

**A finished state must read as finished.** The same section headed *"Nameservers wijzigen bij
OXXA"* over a register already holding Cloudflare's pair, with the form pre-filled with the values
it was showing one line above: an outstanding action where there was none, on the *most common*
end state this integration has — a zone adopted from a client who was already on Cloudflare, or a
push that worked weeks ago. So the panel compares the two and says so instead
(`oxxa.push.nothing_to_change`, with "Toch wijzigen" opening the form anyway: moving a domain
*off* Cloudflare is a legitimate push, and "there is nothing to do" must not become "you may not").

Three things decide it, and each is a way of getting it wrong:

- **`ns_observed`, never `ns_desired`.** What we last *asked* for is not what the register holds;
  reading the wish would call the delegation finished the moment a push was sent, which is exactly
  the window where the panel is worth watching.
- **A set, not a sequence** (`sameNameservers`). A registrar returns its nameservers in whatever
  order it stores them, and comparing joined strings would call an unchanged domain changed. Case
  and the root dot are not part of a hostname's identity either. An **empty** side never matches:
  a domain with no Cloudflare zone, or a register never read, must keep the ordinary form rather
  than fall quiet about a delegation nobody has looked up.
- **`drift` / `missing` / `error` keep the form in front of the user.** In each of those the
  delegation can read perfectly correct and still need re-sending — the group backing it is gone,
  the register was edited elsewhere, the last attempt failed.

What is deliberately **not** built is a single button that fires both endpoints. The composition
is a pre-filled form, which is better than an orchestration: the user sees exactly what will be
pushed before it is, each leg reports its own outcome, and a failed second leg leaves a finished
first leg rather than an ambiguous half-success.

**What a half-applied connect looks like, and how to finish it:**

| state | how it reads | how to finish it |
|---|---|---|
| zone connected, push never sent | **no `oxxa` issue at all.** `ns_desired` is NULL, so nothing claims a delegation is outstanding — the panel simply shows what the registry holds, or `never_synced` / `not_in_register` while no sync has matched the domain | push; connect is a no-op that returns the adopted zone |
| push refused | HTTP **200** with `ok:false` and an error key (§10); the row carries `push_error`, `ns_desired` and `last_error` | retry the push alone — the desired set was persisted, so nothing is retyped, and the group the failed attempt created is found by alias rather than duplicated |
| push sent, registry not caught up | `not_delegated_yet` | wait; refresh the domain to re-read the registry |
| pushed, then edited at OXXA by hand | `nameserver_drift` on the next sync | decide, then push again — nothing auto-reverts |
| group exists but holds something else | `errors.oxxa_nsgroup_conflict`, the push refusing outright (§4) | look at the group in OXXA's portal; we will not rewrite it |

Nothing is ever half-applied *at OXXA*: the worst outcome of an interrupted push is an unused
nameserver group, which costs nothing and is found again by the next attempt.

## 8. `GET /domains/{id}/status` and its issue keys

`GET /api/v1/oxxa/domains/{id}/status` reads **stored rows only** and never calls OXXA — a domain
page must not wait on an outside API to render, and must still render when the registrar is down
(`docs/PERFORMANCE.md`). `POST /domains/{id}/refresh` is the explicit "go look" action, mirroring
the domains module's own refresh. Same split as `cloudflare`'s `status` / `check`.

`issues` are stable keys the client resolves to `oxxa.issue.*`:

| key | what it found |
|---|---|
| `no_account` | no **active** OXXA credential — an org whose only account is switched off reads the same, because it can act through neither |
| `never_synced` | an active credential exists, no sync has ever run |
| `not_in_register` | synced, and this domain is in none of the tenant's registers — ordinary for a domain registered elsewhere |
| `expiring_soon` / `expired` | inside `EXPIRY_WARNING_DAYS` (60), or past it |
| `transfer_unlocked` | the registrar reports the transfer lock off |
| `autorenew_off` | the registrar reports autorenew off — how a domain quietly lapses |
| `nameserver_drift` | the registry holds something other than what we pushed |
| `nameservers_missing` | we pushed a delegation and the registry reports none |
| `push_error` | the last push was refused; `last_error` holds OXXA's own words, or the client's own sentence where the refusal was ours (§2) |
| `not_delegated_yet` | the registry and public DNS disagree — normal during propagation, and only ever raised when public DNS actually answered (§5) |

`configured` is separate from the issue list: it is what tells the panel to offer "configure"
rather than "sync".

**There is deliberately no "desired but never pushed" key.** `ns_desired` is written in exactly
one place — `push_nameservers` — which sets `active` or `error` in the same breath, so the state
the key would name is unreachable. It existed briefly as `nameservers_not_pushed` and was
removed: an issue key nothing can raise is copy that documents a lie, and a reviewer who trusts
it will look for a bug that is not there. The honest reading of "the zone is connected and we
never pushed" is *silence* (§7).

## 9. Permissions (§15)

**The issue names two keys; the module ships three.**

| key | covers |
|---|---|
| `oxxa.settings.manage` | add / rotate / verify / delete a reseller login, and read the settings screen |
| `oxxa.registrar.sync` | run a register sync, read the stored register and one domain's status, refresh a domain, and the account *picker* |
| `oxxa.registrar.manage` | push a domain's nameservers — the one thing here that changes the outside world |

`oxxa.registrar.sync` and `oxxa.registrar.manage` are the two #296 named. The third exists
because acting *through* the configured reseller login and *replacing* that login are not the
same act: the second repoints schakl at a different register entirely. `cloudflare.settings.manage`
draws exactly this line around its API token and `google.settings.manage` around the OAuth
client. A settings screen also needs a permission to gate on, and gating it on `registrar.manage`
would mean everyone who may push nameservers may also read which credentials exist.

All three are **admin-only by default and never `client`**. `domains.domain.write` already
excludes the client role, but reusing it would have been wrong for a different reason: it edits
*our record of* a domain, while these read a register and repoint a client's live delegation. A
tenant who widens "may edit a domain" to every member must not silently also hand out "may move
this client's nameservers".

**Company horizon (#285).** Neither table carries `company_id`. `OxxaDomain`'s client is its
*domain's*, so it declares `__company_horizon_clause__` — without it the repository's column match
finds nothing and therefore filters nothing at all, and a membership scoped to one company group
would read the whole register. `OxxaAccount` is org-wide configuration with no client of its own
and stays behind its own admin-only manage permission, exactly as §15 describes for config
surfaces. Every cross-module read of `domains` that takes an id or a name **from the caller**
carries the predicate itself — `OxxaService._domain_or_404` and `_domain_ids_by_name` (failure
mode 3), the latter covering the sync's name matching, because a restricted membership must not
learn a domain exists by watching a sync match it. The third such read, `_domain_names`, is
horizon-safe by construction rather than by predicate: the ids it resolves come out of register
rows already filtered by `scoped_select()`. Anything that feeds it ids from somewhere else has to
add the clause.

## 10. Errors

`message` in the error envelope is always an i18n key (§9), so OXXA's own text never goes in it —
it is not translatable. Where the operation still commits — verify, sync, and a refused push — the
redacted text is persisted to the row's `last_error`, truncated to 500, which is what the settings
screen and the domain panel render.

| key | when |
|---|---|
| `errors.oxxa_no_account` / `errors.oxxa_account_inactive` | nothing to act through (409) |
| `errors.oxxa_account_ambiguous` | several active registers and no `account_id` — this module never picks (409) |
| `errors.oxxa_not_verified` | the account has no cached TLD list, so no domain can be addressed (409) |
| `errors.oxxa_unknown_tld` | the name does not split against the register's own suffix list (409) |
| `errors.oxxa_domain_not_in_register` | refresh asked for a domain OXXA does not hold (409) |
| `errors.oxxa_credential_rejected` | HTTP 401/403, or the best-effort prose sniff on an `XMLERR` (409) |
| `errors.oxxa_credential_unreadable` | the stored secret will not decrypt — re-enter it (409) |
| `errors.oxxa_nsgroup_conflict` | a `schakl-…` group exists at OXXA holding **different** nameservers: somebody edited it by hand, and no retry fixes it (§4). Mapped as 409, but the only path that can raise it today is the push, which reports it as the `error` of a 200 `ok:false` — see below |
| `errors.oxxa_unreachable` | the catch-all for *no HTTP status and no OXXA status code* (502) — see below |
| `errors.oxxa_request_failed` | OXXA answered and refused: an `XMLERR` envelope, or an HTTP ≥ 400 that was not 401/403 (502) |
| `errors.invalid_hostname` / `errors.invalid_nameserver_count` | field errors on the push payload (422) |

**`oxxa_unreachable` is wider than "transport", and the name undersells it.** `_translate` picks
it whenever an `OxxaError` carries neither an HTTP status nor an OXXA status code, and six
client-side refusals have exactly that shape: a response over `MAX_RESPONSE_BYTES`, a body
carrying a `<!DOCTYPE`/`<!ENTITY` (refused unparsed), XML that will not parse, an envelope with
no `<order>` node, `nsgroup_add` returning no handle, and a nameserver count outside 2–6 that
reached the client (the push schema refuses that first, so it is belt-and-braces). All six mean
*we could not make sense of the answer*, and the advice is the one a real transport failure gets —
try again in a moment — so a single key is right. What would be wrong is reading an
`oxxa_unreachable` in a log as proof the network was down. `oxxa_nsgroup_conflict` was carved out
of exactly this bucket because it is the one member no retry can ever fix.

### What raises, and what comes back as a result

**`verify`, `sync` and `push_nameservers` answer a registrar failure with a result object, not an
exception.** For the first two the reason is presentational: "OXXA said no" is a fact to report on
the settings screen, not a 500 three screens away, so they return `ok=false` carrying the redacted
text.

For the push the reason is **mechanical, and it is the one contract here a maintainer must not
have to rediscover**. `require_context` rolls the session back on *any* exception, so raising
after the except-branch has written `ns_desired`, `ns_push_status="error"` and `last_error` would
discard precisely those rows: the panel would come back with an empty form, the user would retype
nameservers they had already typed, `NameserverPushStatus.ERROR` would be unreachable and
`push_error` (§8) would be an issue key nothing could raise. So a refused push is **HTTP 200**
with `{ok: false, changed: false, nameservers: [...], error: "<i18n key>"}` — the same shape
`verify` and `sync` use — and the retry state is on the row. Do not "tidy" this into a raise.

**Pre-flight errors still raise**, because nothing has been written yet and there is nothing to
lose: `404` for an unknown or out-of-horizon domain, and `oxxa_no_account`,
`oxxa_account_ambiguous`, `oxxa_account_inactive`, `oxxa_not_verified`, `oxxa_unknown_tld` and
`oxxa_credential_unreadable` (all 409). `refresh_domain` raises throughout, including for the
registrar call itself — it writes nothing before that call returns, so an exception costs nothing.

The `sld`/`tld` split refuses rather than guesses, for two reasons that both end in the wrong
object being addressed at the registrar: an unverified account has no suffix list at all, and
`shop.klant.nl` is a hostname inside a zone, which naive surgery would read as `shop` + `klant.nl`.
`split_suffix` returns `None` when what remains is not a single label.

## 11. Testing, and what is not here

`client.set_transport` is the only network seam, and `tests/oxxa_fake.py` is the stateful fake a
fixture installs into it — modelled on `tests/cloudflare_fake.py`, holding the register, the
nameserver groups and the identities in plain dicts and answering `command.php` from them. Left
unset, a test that forgot to stub fails loudly on connect instead of quietly reaching
`api.oxxa.com`. `tests/test_oxxa_api.py` runs against it: tenant isolation on the accounts, the
register and the push; the deny-by-default sweep as a member holding nothing; the write-only
password surviving an unrelated `PATCH`; a provider error carrying a URL being redacted before it
is stored; the `sld`/`tld` refusals; the find-or-create rule, including the assertion the whole
integration hangs on — that `nsgroup_upd` is never sent; sync's match/unmatched/drift arithmetic;
refresh reading the two things a register-wide sync cannot afford; and `status` answering without
touching the registrar at all.

Three things the fake does on purpose, each guarding a hazard rather than a behaviour: it records
the parsed command and the **non-credential** parameters only and never the request URL (a fake
that logged `str(request.url)` would put the tenant's API password in every pytest failure — the
exact leak `redact` exists to prevent, reintroduced in the harness, and one test asserts it); it
returns **bytes** with the ISO-8859-1 prologue, so the client keeps being tested on parsing bytes
rather than an httpx-guessed `str`; and it answers `domain_ns_upd` with
`<order_complete>FALSE</order_complete>` on **success**, as OXXA's own documented example does, so
anyone who "fixes" the client to read `order_complete` turns every push test red. The status
tokens are deliberately spaced inconsistently (`XMLOK 16` vs `XMLOK18`), as the document's
examples are.

**What that suite does and does not prove.** The fake is written from the same document as the
parser, so it proves the parser agrees with the document — not with OXXA. Every item on §1's
checklist is a place where those two can differ and no test can tell. When a credential arrives,
capture real responses and re-cut the fake from them; that is the change that turns this suite
from a consistency check into evidence.

The web half is built too, and listed here so a reader knows where all of it is: the panel, its
actions and its generated types under `apps/web/src/lib/modules/oxxa/`, the settings screen at
`apps/web/src/routes/(app)/settings/oxxa/`, the `oxxa.*` keys in both `messages/en.json` and
`messages/nl.json` (Golden Rule 2 — the issue keys of §8, the permission labels, the activity
sentences), the `settings-nav` entry gated on `oxxa.settings.manage`, and the three
`LICENSE-COMMERCIAL.md` covered-directory entries alongside the per-directory `LICENSE` markers.

One naming rule that has to survive a regenerate: this module's account schemas are
`OxxaAccountRead` / `OxxaAccountOption` / `OxxaAccountCreate` / `OxxaAccountUpdate` /
`OxxaAccountVerifyResult` / `OxxaAccountSyncResult`, **not** `AccountRead` and friends, because
`cloudflare` already publishes those component names. Two components sharing a name make FastAPI
qualify *both* into `app__modules__…__schemas__AccountRead`, so the collision rewrites the **other**
module's generated types and its `types.ts` with them. Prefixing this half keeps
`cloudflare/types.ts` reading the plain names it always read; `oxxa/types.ts` strips the prefix
back off once, so components here still read `AccountRead`.

Genuinely not here, and each one a deliberate line rather than an oversight:

- **A company-hub panel.** The registrar's facts belong to a *domain*, so the whole working
  surface is one `EntityPanelSpec` on the domain detail page (§7) plus org-wide configuration
  under Instellingen, where `docs/UX.md` principle 6 puts it. The module contributes no nav item
  for the reason `cloudflare` contributes none: a registrar is not a place you go.
- **A generic `external_id` mapping table.** The seam carries provider references as
  `RegistrarDomain.nameserver_ref` and `RegistrarContact.ref`, stored on the register row. A
  separate mapping table is only worth it when a second registrar arrives and needs one.
- **`create` / `transfer` / `renew`.** Absent from `RegistrarProvider` on purpose: they spend money
  and are irreversible. A later issue that wants them should extend the protocol consciously rather
  than inherit the power by accident. Same for writing DNSSEC or an identity — the identity in
  particular is shared, so an edit would rewrite the WHOIS of every domain behind the handle.
- **A background sync cron** (§3): no `cron_jobs` on the module descriptor, on purpose.
- **`domain_check`**, and therefore any availability search — the one command with a published
  quota (§3) is the one command this module never sends.
- **A second registrar.** `app/core/registrar/` exists so that costs a file rather than a
  refactor, but `known_registrars()` returns exactly one key today, and a seam with one
  implementation is a hypothesis. The second one is what tests it.
