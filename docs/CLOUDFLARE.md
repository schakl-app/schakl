# Cloudflare integration

> The `cloudflare` module (epic #278): per-client DNS, domain-wide redirects and Pages linking,
> through the tenant's **own** Cloudflare accounts. Business-licensed (`sku="cloudflare"`).
> Read this before changing anything under `apps/api/app/modules/cloudflare/`.

Not to be confused with **`app/core/cloud/cloudflare.py`**, which talks to Cloudflare with the
*operator's* instance token about the *operator's* zone (Cloudflare for SaaS custom hostnames,
epic #199, `docs/CLOUD.md`). Opposite posture in every respect that matters: that credential is
environment config and instance-wide, this one is tenant data and there are several. They share
no code on purpose.

## 1. What it is for

`Domain.status = redirect` and `Domain.redirect_url` have existed since the domains MVP (#90) as
a status/URL pair with **nothing behind them** — the actual redirect was wired by an external
n8n/webhook flow (#96). This module gives that pair a real mechanism for domains that live on
Cloudflare, and adds the DNS and Pages surfaces an agency needs around it.

## 2. The credential is a row, not a setting

`cloudflare_accounts` holds one row per Cloudflare account the tenant works with. An agency has
its own and some clients bring theirs, so a per-org singleton would have been wrong on day one.

- **A scoped API token**, never the legacy Global API Key. Fernet at rest (`app.core.crypto`,
  the `*_encrypted` convention from `docs/GOOGLE.md`), write-only through the API: the response
  carries `token_configured` and never the value.
- `provider_id` links the row to the tenant's own provider catalog (#89) purely as a label.
- Deleting an account cascades the *local* rows (zones, redirects, Pages projects and links) and
  **touches nothing at Cloudflare**. Deleting a client's live zone as a side effect of tidying a
  credential list is unrecoverable.

### Token scopes

`POST /accounts/{id}/verify` probes what the token can actually do and stores the answer, so a
missing scope reads as *"Zones uitlezen: niet toegekend"* on Instellingen → Cloudflare instead of
as a 403 at a button three screens away. Only four things can be observed cheaply
(`client.CAPABILITIES`); everything else needs a zone in hand and is reported by the real error.

| Cloudflare permission | Needed for |
|---|---|
| Zone → Zone → **Read** | listing and adopting zones (the minimum) |
| Zone → DNS → **Edit** | the DNS table, the export, the redirect placeholder records |
| Zone → **Single Redirect** → **Edit** | reading and writing the domain-wide redirect rule |
| Zone → Page Rules → **Read** | *optional*: detecting a legacy forwarding Page Rule as a conflict |
| Account → Zone → **Edit** | *optional*: creating a zone that does not exist yet |
| Account → Cloudflare Pages → **Read/Edit** | *optional*: the Pages project list and hostname linking |

**The redirect scope's name has moved and this table used to name the old one.** Cloudflare's
token editor calls it *Single Redirect* today; the API permission list calls the same thing
*Dynamic URL Redirects Write*, and this file said *Dynamic Redirect*, which is what it was called
when the module was written. An admin hunting a label that no longer exists tends to grant
*Transform Rules* instead — a genuinely different permission that does not cover the
`http_request_dynamic_redirect` phase — and then reports, correctly as far as they can see, that
the token has the right permissions. Cloudflare states the minimum on
[Create a redirect rule via the API](https://developers.cloudflare.com/rules/url-forwarding/single-redirects/create-api/);
that page is the authority, not this table, and reports of it still needing more are common
enough that the module must degrade rather than argue.

A token scoped to less is **degraded, not broken**. Every probe in the status check fails softly
and names itself in `unavailable`, because losing the whole screen to one optional 403 pushes an
admin to mint a wider token than they need.

### The two scopes the buttons use were the two the screen never mentioned

`CAPABILITIES` listed `token_valid`, `accounts_read`, `zones_read`, `pages_read` and
`registrar_read` — five things an admin could read a ✓ against while the redirect button
answered *"Cloudflare weigert het API-token"*, because neither DNS nor the redirect ruleset was
ever probed. That is the whole content of "the token seems to have the right permissions": the
screen agreed with them.

`dns_read` and `redirect_read` close it, and they carry two properties worth keeping. **They are
reads, and the label says so** — an *edit* scope cannot be observed without writing something,
and this module will not create a record on a client's zone to find out; what a read catches is
the case that actually happens, a permission never granted at all. And **they need a zone to
address**, so they are *absent* from `capabilities` rather than `False` when the account has
synced none (`client.ZONE_CAPABILITIES`, rendered as *niet gecontroleerd*). "We did not look" and
"not granted" send an admin to different places, which is §6's rule about Pages links stated
about a token instead of a hostname.

### Failing softly is not the same as forgetting

Every probe fails softly, and until `capability_errors` each one also **discarded what Cloudflare
said**. A refused capability was recorded as a bare `False`: no status, no code, no text, nowhere —
not on the row, not in a log. On the screen that is *"DNS van een zone lezen: niet toegekend"*, one
sentence that reads as *add this permission* whatever actually happened, printed beside a Cloudflare
token screen that grants DNS Read. Nothing about it can be checked, so the only move left is
re-minting a credential that was never the problem — the same dead end as 9109, arrived at from the
other direction.

`probe_capabilities` now returns a third value, capability → `describe_failure(exc)`: **status,
numeric code and Cloudflare's own words**. All three matter and none of them is in `str(exc)` —
*"Actor is not authorized"* is a scope to add, a `400` naming a query parameter is our bug, `9109`
is neither. It is stored on the account beside `capabilities`, keyed identically, replaced whole by
each verify (a stale explanation beside a fixed permission is worse than none) and rendered under
the ✗ list. Cloudflare's text is untranslatable and never enters an error envelope (§9); it is
evidence, and it goes where the ✗ is — the rule `last_error` already follows on the redirect panel.

Same reasoning one layer out, for `sync`: a Pages or Registrar read that fails is collected into
`warnings`, and the settings screen worded only two known keys and dropped the rest. So an
unreadable Pages list and an empty one both printed *"0 Pages-projecten"* — §17's "a silent zero
reads exactly like nothing found", against the one number an agency uses to decide whether the
integration works. Whatever the screen cannot word itself is now printed in Cloudflare's words.

**Two things the zone probes still cannot settle**, and both need a live credential rather than a
guess (the `docs/OXXA.md` §1 pattern):

- **`per_page` is gone from them.** A probe should differ from the call it stands in for as little
  as possible, and `per_page=1` was the only thing here that no real call does — `paginate` sends
  50. Cloudflare's current OpenAPI schema declares `minimum: 1` for `dns-records_per_page`, so it
  *should* be accepted; the retired `api.cloudflare.com` reference documented a minimum of **5** for
  that same endpoint. "Should" is not evidence, and the parameter bought nothing.
- **`GET /zones/{id}/dns_records` answers auth failures with `400`, not `401`/`403`** — observed
  directly: a bogus token gets `400` with code `9106`, *"Authentication failed"*. `_unwrap` classes
  only 401/403 as `CloudflareAuthError`, so a genuine *scope* problem on this endpoint would surface
  as `errors.cloudflare_request_failed` (and a `502`, which blames Cloudflare) rather than as the
  scope message. Not mapped yet on purpose: whether `9106` also means "this token may not do this
  here", or only "this is not a token", is exactly the kind of thing this module has twice guessed
  wrong. The probe now records the code, so the next real occurrence answers it.

### No probe is the gate

Cloudflare has **two kinds of API token and they do not verify at the same URL**. A *user* token
answers `GET /user/tokens/verify`; an **account-owned** token — the newer kind, owned by the
account rather than by a person, which is exactly what an agency mints so the integration does not
leave with whoever created it — is refused there with `401` and code `1000`, *"Invalid API Token"*,
while working perfectly for every zone, DNS and account call it is scoped for. It verifies at
`GET /accounts/{account_id}/tokens/verify` instead. So `verify_token` takes an account id and asks
the second endpoint when the first refuses, and `probe_capabilities` reads `/accounts` **first**,
precisely so it has an id to address that call with.

The endpoint was the bug; treating one probe as the gate is what made it fatal. `probe_capabilities`
ran verify first and raised for everything behind it, so a valid credential read *"Token problem:
Invalid API Token"* on a screen whose zone list was filling in beside it. Two rules replace it:

- **A read that succeeds outranks a verify that refuses.** "Cloudflare will describe this token to
  me" is a strictly narrower question than "Cloudflare accepts this token", and only the second one
  matters — the reads *are* the calls this module makes. `token_valid` is therefore true when any
  probe was answered, and no probe raises on its own.
- **"Invalid" is only honest when every probe was refused.** That is the one state where the word
  describes the token rather than one endpoint's opinion of it.

The same shape is why the fake had to change: it modelled a dead token as "verify says no,
everything else works", which is not a Cloudflare that exists. The only test that could have caught
this was passing against a fiction. A token the fake rejects is now rejected at every path.

### A valid token, refused from here (403 / 9109)

The third way a perfectly good credential reads as a dead one, and the most expensive so far.
Cloudflare tokens carry an optional **Client IP Address Filter**, and a call from an address
outside it answers `403` with code `9109`, *"Cannot use the access token from location: <ip>"* —
at **every** endpoint, both verify URLs included. Every probe fails, `capabilities` comes back
empty, and the row is indistinguishable from a revoked token unless somebody reads the code.

It was worse than indistinguishable, because `_translate` matched `CloudflareAuthError` **before**
consulting `_ERROR_CODES`. That ordering made the whole code table unreachable for every 401 and
403, so 9109 came out as `errors.cloudflare_token_rejected` — *"check that it is valid and has the
required permissions"*, the one sentence that cannot lead to the fix. The token is valid, the
permissions are right, and the repair is one line in Cloudflare's own token screen. The code is now
read first and the auth class is the fallback: **a refusal Cloudflare gave a number to is better
evidence than the status that carried it.**

Two smaller rules fall out, and neither is about Cloudflare.

- **"Only a 401 marks the row broken" needed exactly one exception, and it is a 403.** That rule
  (below) is right because a 403 normally means *not scoped for this call* — degraded, and already
  reported per capability. 9109 is a 403 that means *nothing works at all*, so `_token_is_broken`
  names the two cases together and `_flag_account` / `_record_failure` both take it from there.
  Under the old rule the screen stayed green over a credential that could not read one zone.
- **The failure is invisible to the half of the module that would have caught it.** The zone sync
  raises and stops, so nothing downstream runs and Pages and Registrar report zero — which reads
  exactly like *"this account has no Pages projects"*. §6's blank-panel trap, arrived at from a
  different direction: the counts were honest and the conclusion a reader drew from them was not.

`tests/cloudflare_fake.py` models it as its own state (`ip_blocked`) rather than as a `deny`
fragment, because the two are opposites: a denied fragment is one endpoint this token may not
touch, and this is a token scoped for everything and refused for all of it. A fake that can only
express the first is a fake in which this failure does not exist — the lesson the account-owned
token taught one section up, in a second costume.

### The flag is two-way

`_flag_account` marks a row `error` on a path that still commits, and **only a 401 does it**.
`CloudflareAuthError` covers both of Cloudflare's refusals and they are different sentences: 401 is
"I do not accept this token at all", 403 is "not scoped for *this call*" — degraded, not broken,
and already reported per capability. Flagging the 403 left a DNS-only token reading "Token problem"
for ever over an optional Pages probe it was never meant to pass. The text is still recorded (a
missing scope is worth reading); it is the red status it does not earn, so the screen labels the
two differently.

And a note written on a path that then **raises** must not ride the request transaction.
`require_context` commits on the way out and rolls back on any exception, so `sync_account`
writing `last_error` and then raising recorded nothing at all — the update was undone by the very
error it described, and the row read healthy while the admin looked at a red toast.
`_record_failure` writes it on its own session and commits it there, deliberately surviving that
rollback. It is the one write in this module that does not ride the scoped repository, so RLS is
bound the way `app.core.jobs` binds it and the org is pinned in the `WHERE` too; and a failure to
write the note is logged and swallowed, because losing the note is bad and losing the exception
is worse.

`_clear_account_error` is the mirror, and its absence was the other half of the same complaint.
The flag used to be one-way — nothing but a manual re-verify took a row out of `error` — so a token
that had been fixed at Cloudflare, or was never broken at all, kept its red line through every sync
and check that plainly worked. A successful zone read now clears it, because that read is the same
evidence a verify would have been.

## 3. Two rules the module never bends

**Never guess which account.** The same apex may exist in several of the tenant's accounts —
Cloudflare makes *activation* exclusive, not creation — so `cloudflare_zones` is unique on
`(org_id, cf_zone_id)` and never on the name. Sync refuses to match a second zone onto a domain
another zone already claims; `connect` refuses to pick and answers `cloudflare_zone_ambiguous`.
This is not caution for its own sake: a zone created in the wrong Cloudflare account cannot be
moved, only deleted and recreated, with a nameserver change and a propagation window in between.

**Observe before you write.** `connect` adopts an existing zone before it considers creating one
(an agency taking over a client's existing Cloudflare setup is the normal case). The redirect is
*appended* to the zone's entrypoint ruleset, never PUT over it, so the tenant's own redirect rules
survive. And a reconcile **reports** drift rather than overwriting it.

## 4. Domain-wide redirects

One Redirect Rule ("Single Redirect") per zone, in the `http_request_dynamic_redirect` phase.
Zone-scoped, available on every plan including Free — Bulk Redirects were considered and rejected
because they need account-level scopes and make per-zone drift detection much harder.

- The expression is built in `redirects.py`, deliberately free of I/O so it can be asserted on:
  `(http.host eq "klant.nl" or ends_with(http.host, ".klant.nl"))`. `ends_with`, not a substring
  match, so `nietklant.nl` is never caught.
- `preserve_path` compiles to `concat("<target>", http.request.uri.path)`; without it the target
  is sent as a static value, which is what makes the common "everything to the new homepage" case
  readable in Cloudflare's own dashboard.
- **A redirect pointing back into its own match set is refused** (`cloudflare_redirect_loop`).
  Cloudflare saves that rule happily; the browser reports `ERR_TOO_MANY_REDIRECTS` and the
  client's site is down. Note it depends on `include_subdomains`: `klant.nl → nieuw.klant.nl` is
  sensible with subdomains off and a loop with them on.
- **The rule only fires for traffic that reaches Cloudflare's edge.** A zone whose apex has no
  *proxied* record receives none: the rule saves, the dashboard shows it as active, and nothing
  happens. By a distance the most confusing failure this feature has, so `ensure_origin` (on by
  default) adds Cloudflare's documented placeholder — a proxied `AAAA 100::` — where there is no
  proxied record, and the status check reports `origin_missing` when someone greys the cloud
  later. An existing record is never replaced.
  **The placeholder is a second step, and a step that can fail on its own scope must be allowed
  to.** It writes DNS, which is a different token permission from the one that writes the rule,
  and it used to run inside the same `try` — so a token scoped for redirects and not for DNS
  failed the whole request *after the rule had already been created at Cloudflare*. The raise
  rolled the transaction back, so schakl kept no record of the rule it had just made: the panel
  still showed no redirect, the next press appended a second rule to a live client's zone, and
  the next a third, each one reported as "Cloudflare rejected the token". Two rules generalise.
  **A failure after the durable half is done is a note, never a rollback** — the rule exists and
  it is what was asked for, so the row is written and the refusal is recorded on it
  (`CloudflareRedirect.last_error`, rendered on the panel with Cloudflare's own text, because
  *which* permission is missing is a sentence only Cloudflare can write). And **an optimisation
  must not be able to fail its own precondition**: the missing origin is already a finding of its
  own (`origin_missing`), so nothing is lost by letting the check report it.
  **The placeholder covers the apex and `www`, and deliberately nothing else.** The rule matches
  every subdomain, but a set of them cannot be enumerated; `www` is the one that is always meant.
  The two hostnames also fail *separately*, so they are checked separately: a proxied apex beside
  an unproxied `www` leaves exactly the hostname this feature exists to catch serving nothing,
  while every other signal on the page reads healthy (`origin_www_missing`).
- Setting a redirect also sets `Domain.status = redirect` and `Domain.redirect_url`, and removing
  it walks them back **only if they still say what we put there**. Two screens disagreeing about
  whether a domain redirects is a bug, and a status somebody has since changed by hand is theirs.

## 5. "It already redirects, but not through us"

`POST /domains/{id}/check` is the only call that talks to Cloudflare, and it is what answers that
question. `GET /domains/{id}/status` reads stored rows only — a domain page must not wait on an
outside API to render (`docs/PERFORMANCE.md`), and must still render when Cloudflare is down.

**A stored answer has to say how old it is.** That is the price of the cheap read, and the panel
was not paying it: "geen conflicten" from a check that ran in March and one that ran a minute ago
are the same sentence, and only one of them means anything. `checked_at` is the newest of the
observations the report is *assembled* from — `zones.last_synced_at`, the redirect's
`last_checked_at`, each Pages link's — and never a stamp taken when the request ends. Every probe
fails softly and separately, so a check can come back having read nothing at all, and "gecontroleerd
zojuist" over that report is the one thing it does not know. Reading it off the rows also makes it
the single number both branches of the panel need: a domain served from Pages with its DNS
elsewhere has a check button and no zone.

The report's `issues` are stable keys the client resolves to `cloudflare.issue.*`:

| key | what it found |
|---|---|
| `not_connected` / `no_account` | nothing to check yet |
| `duplicate_zone` | this apex exists in more than one of the tenant's accounts |
| `zone_pending` / `zone_paused` | Cloudflare has the zone but is not serving it |
| `nameservers_not_delegated` | public DNS still points elsewhere — and **only when it definitely does**, see below |
| `redirect_not_pushed` / `redirect_missing` / `redirect_drift` | our rule was never sent / is gone / has been edited at Cloudflare |
| `redirect_conflict` | some *other* rule on the zone redirects: another rule in the ruleset, or a legacy forwarding Page Rule |
| `origin_missing` | the rule exists and no traffic reaches it |
| `origin_www_missing` | the apex reaches the rule and `www` does not — its own key, because `origin_missing`'s "no traffic reaches it" would be false here. Raised only while `include_subdomains` is on; with it off the rule never matches `www`, so an unproxied one is the configured state |
| `domain_says_redirect` / `cloudflare_says_redirect` | the domain record and Cloudflare disagree — how a redirect wired outside schakl (#96) shows up, and how a hand-deleted rule does |
| `token_error` | part of the check could not run; `unavailable` names which parts |

**A finding raised for an unconnected domain has to render there.** The panel drew its issues box
inside the connected branch only, so `domain_says_redirect` and `duplicate_zone` — the two keys
that are *most* meaningful before a zone exists — were computed by the API and dropped by the
client. A domain marked `redirect` here with nothing behind it at Cloudflare is the #96
webhook-era state this whole module exists to replace, and it was the one state with no way to
discover it. The not-connected branch now renders the same list minus `not_connected`, which the
paragraph above it already says.

### Reporting a rule you cannot claim is half an answer

`redirect_conflict` named the rule an agency inherits and offered nothing to do about it, and the
one button on the screen made things *worse*: saving appends a second rule to the same phase, where
Cloudflare takes the first match top-down — so the obvious press left a client's zone with two
redirects and, quite possibly, no change in behaviour at all. Meanwhile `domain_says_redirect` sat
underneath saying the domain redirects and schakl owns nothing, which was true and unfixable.

`POST /domains/{id}/redirect/adopt` closes it: schakl takes ownership of the rule that is already
there. Each half is a refusal, and each one is why this is safe to put on a client's live zone.

- **It writes nothing at Cloudflare.** Adoption is a claim about a rule, not a change to it —
  nothing created, updated, re-ordered or deleted. The worst case of a wrong adoption is a wrong
  row here, which one delete undoes; the test asserts the whole call makes no non-`GET` request.
- **Only a rule identical to what we would have written** (`rules.compare` against
  `rules.build_rule` — the *same* builder the save uses, via `_desired_rule`, or the comparison
  would be against a rule schakl does not actually write). "Adopt whatever is there" would import
  somebody's 302-with-query-dropped as this domain's redirect, and the next ordinary save would
  then "fix" a live client's redirect to something nobody asked for. A difference comes back as
  `errors.cloudflare_redirect_differs` **with the field names**, so the admin either matches the
  intent to the rule or saves and overwrites it deliberately.
- **Never over a rule we already own.** If our stored rule is still live, adopting another would
  orphan ours — a rule on a client's zone that nothing here knows about, which is the state §3
  exists to prevent.
- **By id, never by description** (`find_our_rule`), so a rule that vanished between the report and
  the press is a 404 rather than a stored redirect pointing at nothing.

The panel offers it on the conflict row itself, and only where it can succeed: not for a Page Rule
(a different product this module cannot write) and not while we own a live rule — #253's rule that
a control which always refuses is a broken control. The intent it submits is the redirect form's
own fields, which now seed from `domain_redirect_url` when no rule of ours exists: that is exactly
the inherited state, and an empty box there meant retyping a URL both sides already know.
`last_pushed_at` stays `NULL` on an adopted row — we did not push it, and that distinction is the
feature.

### The delegation verdict is tri-state, and half of it is not Cloudflare's to give

"Do the nameservers point here yet?" is answered by two lists, and only one of them comes from
Cloudflare. `expected_nameservers` is the zone's; `observed_nameservers` is what public DNS says,
which belongs to the `domains` module's own resolver — this module still runs no second one. Two
things followed from that and both were wrong.

**A `bool` had no way to say "I do not know".** `dns.fetch_dns` returns `[]` for a timeout or a
SERVFAIL exactly as it does for a domain that truly delegates nowhere (that module made `dnssec`
tri-state for this reason and left the nameserver list flat), and an empty `expected` means
Cloudflare has assigned nothing yet. Either way the answer was `False`, which the panel rendered
as *"Nameservers wijzen nog niet naar Cloudflare. Wijzig ze bij de registrar."* — an instruction
to go and change something that was very possibly already correct, on the strength of a lookup
that never answered. `_delegation` now returns `None` when either side is silent, `_issues` fires
only on a definite `False`, and the panel has a third sentence. The comparison stays an
intersection: a registrar mid-propagation answers one of Cloudflare's pair beside one of the old
host's, and that is delegation happening.

**And "Controleren bij Cloudflare" did not refresh the half it was being judged on.** The
observed list is written by a daily cron (04:30) and by the domains page's own "DNS vernieuwen",
so the obvious sequence — change the nameservers at the registrar, come back, press the Cloudflare
check — compared a freshly-read Cloudflare against an observation up to a day old, and then
printed *"Gecontroleerd zojuist"* over it. The web action now calls `POST /domains/{id}/refresh`
before the check: composed in the page's own action rather than joined in the API, because
`cloudflare` may not reach into `domains` (§6). It is **best-effort** — that route needs
`domains.domain.write`, which a Cloudflare-only admin may not hold, and a 403 there must not cost
them the check they can run. Which is why the report also carries `nameservers_checked_at`, the
observed side's own age: `checked_at` is the Cloudflare half and was never about this one.

Conflicts are **reported, never resolved**. Cloudflare evaluates redirect rules top-down and we
cannot evaluate a tenant's filter expression to know whether it catches this hostname. Naming it
lets the admin decide; silently appending our rule below it would look like it worked.

Account-level **Bulk Redirects** are deliberately not inspected: enumerating list items to find
one hostname is expensive and needs account scopes most tokens will not have. An agency using
them will see our rule and their bulk redirect both apply — worth knowing.

## 6. Pages — a hostname on a project, not on a zone

A Pages custom hostname is registered on the **project**, and a project belongs to an
**account**. That is the whole reason this surface does not wait on a zone: `link_pages_project`
resolves the account from `payload.project_id`, and the zone is consulted only for the second
half of the job.

Both halves matter, and doing only the first is the failure worth naming: Cloudflare leaves a
custom domain at *pending* forever while nothing resolves to `<project>.pages.dev`, which reads
as a Cloudflare problem and is not. So linking registers the hostname **and** writes the CNAME
when the domain has a zone here (`_ensure_pages_cname`, never over an existing record). When it
has no zone, the registration still happens and pointing DNS is the agency's to do at whatever
provider holds the domain — the panel says so rather than hiding the control.

The hostname must be the domain or a subdomain of it
(`errors.cloudflare_hostname_not_in_domain`). Not a formatting rule:
`cloudflare_pages_links.domain_id` is what gives a link its client (#285), so accepting another
client's hostname here would file it under the wrong company.

**The panel drew all of this inside the connected branch, and that was wrong.** A domain whose
DNS lives elsewhere is exactly the domain an agency serves from Pages, so the feature read as
"this domain cannot be served from Pages" for the case it exists to cover; and unlinking a zone
hid links that nothing on the domain page could then remove. The project picker names the
account whenever the tenant holds more than one, which is §3's rule in miniature: two accounts
may each hold a project called `site`, and nothing else on the row says which Cloudflare this
hostname lands in.

### The link button was the table's only writer, and that was the same mistake one layer down

Every other half of this module reconciles; Pages did not. `cloudflare_pages_links` was written
once, by the button, and never looked at again — so `status` was frozen at whatever Cloudflare
answered in the second the link was made. A hostname that finished provisioning read *pending*
forever, one deleted in Cloudflare's own dashboard read as linked, `last_checked_at` described a
check that never ran a second time, and a placeholder attached in that dashboard before schakl
ever saw the account was invisible here. `list_pages_domains` had existed on the client the whole
time with **no callers**. Two paths now use it, and they answer different questions.

**A sync discovers.** `_sync_pages_projects` reads what each project serves and
`_reconcile_pages_links` files it: a hostname that matches a domain record is **adopted**
(`discovered_at`), one that matches nothing is counted and left alone — inventing a domain row
would put a name under a client who never asked for it — and a link the project no longer serves
is marked `missing_at`, never deleted. Adoption is safe *because* it writes nothing at
Cloudflare: it records what is already true there, which is the same posture "connect" takes when
it adopts an existing zone rather than creating one. Matching is longest-suffix
(`_host_candidates`), so a tenant holding both `klant.nl` and `shop.klant.nl` never gets
`www.shop.klant.nl` filed under the parent — the wrong client's page. Cloudflare embeds a
project's custom domains in the project object, so the normal path costs no extra call; a payload
without the key falls back to one call per project, capped at `PAGES_DOMAIN_SCAN_LIMIT` and
**reported as a warning** when the cap bites, never silently truncated (§17).

**A check refreshes.** `_refresh_pages_links` runs from `domain_status(live=True)` — one call per
distinct project, statuses and error messages written back, sibling hostnames of *this* domain
adopted. It sits outside the `zone is not None` branch for the reason the panel does: a Pages
hostname hangs off the project's account, so inside it the one domain whose DNS lives elsewhere
could never refresh at all. Which also means the panel needs a check control that a domain with
no zone can reach — the connected branch's button is not reachable from there.

Three rules hold the reconcile up, and none of them is about Pages.

- **"We did not look" and "it is gone" are different answers.** A project whose hostname list
  could not be read (a token without Pages, an account with no `cf_account_id`) is named in
  `unavailable` and leaves *every* link on it untouched. Only a project that actually answered
  may mark anything missing.
- **Drift is reported, never resolved.** A missing link keeps its row and shows
  `cloudflare.issue.pages_missing`; re-linking is what clears it, and that is a person's
  decision. Deleting the row on one empty probe would erase the only record that the hostname
  was ever ours.
- **`missing_at` keeps the *first* time it went missing.** "Since when" is the question an agency
  asks; restamping it every check answers "just now" forever.

### Only one half of this module needs an account id, and that is why a blank panel looked fine

**Zones are listed without an account id; Pages and Registrar are addressed by one.** A zone sync
therefore works perfectly on a row whose `cf_account_id` is NULL, filling the screen with matched
domains — while the two halves that need an id are skipped entirely. And skipped as a *zero*,
which on the sync banner reads exactly like "this account has no Pages projects".

Nothing but `verify_account` ever filled that id in. So any tenant whose verify had failed — every
account-owned token, before `client.verify_token` learned the second endpoint — kept a NULL id and
a permanently blank Pages panel over a Cloudflare account that was serving their sites, with the
zone half working the whole time and nothing on any screen connecting the two facts. Note that
`_refresh_pages_links` cannot rescue it either: it refreshes links that already exist and returns
early when there are none, because **adoption is the sync's job**. A domain page alone can never
discover a project.

**And the projects had nowhere to be read even once they arrived.** Instellingen → Cloudflare
listed accounts and zones; Pages appeared only as three numbers on the sync banner, which the next
navigation throws away. So "did the sync find my projects?" was answerable on no screen — the rows
existed, and the only surface that showed them was a dropdown on a domain page, which is empty
for precisely the tenant asking the question. The account block now carries its own Pages table,
read-only (a hostname is linked on the domain it belongs to, which is what gives the link its
client), and it names each project's **matched hostnames**: *"we synced your projects"* and
*"your sites are attached to them"* are different claims, and only the second is what an agency
opened the screen to check. `PagesProjectRead.hostnames` is one batched query, never one per
project, and rides the links' own scoped repository so the company horizon still applies.

Two changes, and the second matters as much as the first. `sync_account` resolves a missing id
itself (`_resolve_account_id`) — it holds a client and the answer is one call, so it asks rather
than waiting to be told; **exactly one** visible account is an answer and several is §3's
never-guess case, since picking one would point every later Pages call at the wrong client's
account. And the `no_account_id` warning is now *rendered*: "not asked" and "nothing found" are
different answers (§17), and while three zeros were the only thing on screen, no one could tell
which one they were looking at.

## 7. Registrar — who *pays* for the name (#298)

A zone is not a registration, and this section exists because the difference is money. Cloudflare
will happily answer DNS for a domain the client registered at their own registrar and renews
themselves — which is precisely the domain an agency must never invoice. `cloudflare_zones`
cannot tell you that. The Registrar list can, and it is a different endpoint
(`/accounts/{id}/registrar/domains`), under a different token permission, answering a different
question.

So it gets its own table, `cloudflare_registrar_domains`, and its own authority column,
`cloudflare_accounts.registrar_synced_at` — deliberately *not* `last_synced_at`. Syncing zones
every night says nothing about who pays a registry, and a token scoped to DNS cannot read the
registrar at all. **Only a register that has answered may narrow what schakl invoices.** Until it
has, every undecided domain bills exactly as it did before the feature existed, which is what
makes this safe to ship into an instance that already invoices domains.

`at_cloudflare` is the whole billing decision, and it is derived once at sync time from
`current_registrar` rather than from the row's mere presence: the list also reports domains held
at other registrars. The raw registrar string is stored beside the flag, because "it moved to
GoDaddy last month" is something an agency needs to *read* rather than infer from a boolean that
silently flipped. A registration that leaves the list keeps its row and stops claiming to be ours.

How `domains` consumes this without ever naming Cloudflare is the seam in
`app/core/registrar/presence.py` — each register contributes its own two SQL clauses (*has this
org's register been read?* and *does it hold this row?*) and core composes them. The module owns
the SQL; core owns only the composition; a disabled module never speaks. The resolution rule
itself lives in `app/modules/domains/invoiceable.py`.

| Cloudflare permission | Needed for |
|---|---|
| Account → Domain Registration → **Read** | the Registrar list, and therefore the billing decision |

Missing it is degraded, not broken, exactly like Pages: `registrar_read` reads as *not granted* on
Instellingen → Cloudflare, `registrar_synced_at` stays NULL, and nothing about invoicing changes.

### The first-registration checklist

**This endpoint has never been exercised against a live Cloudflare Registrar account.** Every
field is therefore read defensively — a row whose name cannot be found is skipped rather than
guessed at, a malformed expiry is `None` rather than an exception, and `None` never means `False`.
Run these the day a real Registrar account exists (`docs/OXXA.md` §1's discipline, same reasons):

1. **The name field.** `_registrar_name` tries `name`, `domain_name`, `domain` and takes the first
   that contains a dot. Confirm which one Cloudflare actually sends; a registration attributed to
   the wrong name is worse than one nobody counted.
2. **`current_registrar`'s exact spelling.** `_is_cloudflare_registrar` is a case-insensitive
   substring match on `"cloudflare"`, because the field is a display name and not a slug. Capture
   real values for both a Cloudflare-held domain and one held elsewhere. **This is the assertion
   the invoicing decision rests on** — get it wrong in the permissive direction and a client is
   billed for a domain they pay for themselves.
3. **Pagination.** `list_registrar_domains` goes through `paginate`, which assumes the standard
   `result_info` envelope. Verify against an account with more than one page.
4. **The status and lock fields.** `registry_statuses` is stored as Cloudflare sends it, truncated
   at 255 characters; confirm it is a string and not a list. `locked` / `auto_renew` are
   three-state on purpose — an absent value must not render as "transferable".
5. **What a zone-only account answers.** Confirm that an account which has never registered
   anything through Cloudflare returns an empty list rather than a 4xx. Either is handled, but
   only the first sets `registrar_synced_at`, and that difference decides whether the register is
   allowed to narrow invoicing at all.

## 8. Permissions (§15)

| key | covers |
|---|---|
| `cloudflare.settings.manage` | add / rotate / verify / delete an account, sync its inventory |
| `cloudflare.dns.read` | the zone list, DNS records and export, the status report, the account *picker* |
| `cloudflare.zone.manage` | create or adopt a zone, edit DNS, set or remove the redirect, link Pages |

All three are **admin-only by default and never `client`**. `domains.domain.write` already
excludes the client role, but reusing it would have been wrong for a different reason: it edits
*our record of* a domain, while these edit the domain's live DNS. A tenant who widens "may edit a
domain" to every member must not silently also hand out "may repoint this client's nameservers".
`settings.manage` is separate again because minting a credential is not the same act as using it.

None of the five tables carries `company_id` — they belong to a client *through a domain* — so
each declares `__company_horizon_clause__` (#285 failure mode 1), and the one cross-module read of
`domains` states the horizon predicate in exactly one place, `CloudflareService._domain_or_404`
(failure mode 3).

## 9. Errors

`message` in the error envelope is always an i18n key (§9), so Cloudflare's own text never goes
in it — it is not translatable. Where the operation still commits (verify, sync, check) the text
is persisted to the row's `last_error`, which the settings screen and the panel render. A small
set of Cloudflare's numeric codes get their own key (`_ERROR_CODES`); everything else falls back
to the generic one, because a wrong-but-specific message is worse than an honest generic one.

Worth knowing: a **malformed** credential answers `400/6003`, not `401` — Cloudflare rejects the
header before it looks the token up. That code is mapped to `errors.cloudflare_token_rejected`,
or the message would point at Cloudflare rather than at the token the admin just pasted.

## 10. Testing

`tests/cloudflare_fake.py` is a stateful stand-in installed through `client.set_transport` — the
only network seam. A test sets up "the zone already redirects" by writing the rule into the fake's
`rulesets` and asserts on what the module *reports*; `deny` simulates a token missing a scope,
`account_owned_token` the kind that refuses the user verify endpoint, and `revoked` a credential
disabled at Cloudflare after schakl stored it — flipping that one back is how a test exercises
*recovery*, which nothing could reach while the error flag was one-way.
Nothing in the suite touches the network, and a test that forgot to install the fake fails loudly
on connect rather than quietly hitting `api.cloudflare.com`.

## 11. What is not here

The **registrar half of #278** — OXXA sync, and the write path that pushes a connected zone's
nameservers back to the registrar so "Connect to Cloudflare" becomes one action instead of two.
It needs credentials and real API documentation that do not exist yet, and CLAUDE.md forbids
writing an integration from memory. The seam it plugs into is already here: `CloudflareZone.
name_servers` stores exactly what has to be pushed, and pushing it is a separate, retryable step,
which is also the answer to #278's "decide the failure path explicitly" — the zone is the durable
half, nothing is half-applied, and a retry re-adopts the zone Cloudflare kept.

Also deferred: a background sync cron (today's sync is an explicit button), and Bulk Redirects.
