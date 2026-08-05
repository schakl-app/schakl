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
| Zone → Dynamic Redirect → **Edit** | reading and writing the domain-wide redirect rule |
| Zone → Page Rules → **Read** | *optional*: detecting a legacy forwarding Page Rule as a conflict |
| Account → Zone → **Edit** | *optional*: creating a zone that does not exist yet |
| Account → Cloudflare Pages → **Read/Edit** | *optional*: the Pages project list and hostname linking |

A token scoped to less is **degraded, not broken**. Every probe in the status check fails softly
and names itself in `unavailable`, because losing the whole screen to one optional 403 pushes an
admin to mint a wider token than they need.

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

The report's `issues` are stable keys the client resolves to `cloudflare.issue.*`:

| key | what it found |
|---|---|
| `not_connected` / `no_account` | nothing to check yet |
| `duplicate_zone` | this apex exists in more than one of the tenant's accounts |
| `zone_pending` / `zone_paused` | Cloudflare has the zone but is not serving it |
| `nameservers_not_delegated` | public DNS still points elsewhere (from the domains module's own periodic lookup — this module runs no second resolver) |
| `redirect_not_pushed` / `redirect_missing` / `redirect_drift` | our rule was never sent / is gone / has been edited at Cloudflare |
| `redirect_conflict` | some *other* rule on the zone redirects: another rule in the ruleset, or a legacy forwarding Page Rule |
| `origin_missing` | the rule exists and no traffic reaches it |
| `origin_www_missing` | the apex reaches the rule and `www` does not — its own key, because `origin_missing`'s "no traffic reaches it" would be false here. Raised only while `include_subdomains` is on; with it off the rule never matches `www`, so an unproxied one is the configured state |
| `domain_says_redirect` / `cloudflare_says_redirect` | the domain record and Cloudflare disagree — how a redirect wired outside schakl (#96) shows up, and how a hand-deleted rule does |
| `token_error` | part of the check could not run; `unavailable` names which parts |

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
`rulesets` and asserts on what the module *reports*; `deny` simulates a token missing a scope.
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
