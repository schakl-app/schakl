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
| `domain_says_redirect` / `cloudflare_says_redirect` | the domain record and Cloudflare disagree — how a redirect wired outside schakl (#96) shows up, and how a hand-deleted rule does |
| `token_error` | part of the check could not run; `unavailable` names which parts |

Conflicts are **reported, never resolved**. Cloudflare evaluates redirect rules top-down and we
cannot evaluate a tenant's filter expression to know whether it catches this hostname. Naming it
lets the admin decide; silently appending our rule below it would look like it worked.

Account-level **Bulk Redirects** are deliberately not inspected: enumerating list items to find
one hostname is expensive and needs account scopes most tokens will not have. An agency using
them will see our rule and their bulk redirect both apply — worth knowing.

## 6. Permissions (§15)

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

## 7. Errors

`message` in the error envelope is always an i18n key (§9), so Cloudflare's own text never goes
in it — it is not translatable. Where the operation still commits (verify, sync, check) the text
is persisted to the row's `last_error`, which the settings screen and the panel render. A small
set of Cloudflare's numeric codes get their own key (`_ERROR_CODES`); everything else falls back
to the generic one, because a wrong-but-specific message is worse than an honest generic one.

Worth knowing: a **malformed** credential answers `400/6003`, not `401` — Cloudflare rejects the
header before it looks the token up. That code is mapped to `errors.cloudflare_token_rejected`,
or the message would point at Cloudflare rather than at the token the admin just pasted.

## 8. Testing

`tests/cloudflare_fake.py` is a stateful stand-in installed through `client.set_transport` — the
only network seam. A test sets up "the zone already redirects" by writing the rule into the fake's
`rulesets` and asserts on what the module *reports*; `deny` simulates a token missing a scope.
Nothing in the suite touches the network, and a test that forgot to install the fake fails loudly
on connect rather than quietly hitting `api.cloudflare.com`.

## 9. What is not here

The **registrar half of #278** — OXXA sync, and the write path that pushes a connected zone's
nameservers back to the registrar so "Connect to Cloudflare" becomes one action instead of two.
It needs credentials and real API documentation that do not exist yet, and CLAUDE.md forbids
writing an integration from memory. The seam it plugs into is already here: `CloudflareZone.
name_servers` stores exactly what has to be pushed, and pushing it is a separate, retryable step,
which is also the answer to #278's "decide the failure path explicitly" — the zone is the durable
half, nothing is half-applied, and a retry re-adopts the zone Cloudflare kept.

Also deferred: a background sync cron (today's sync is an explicit button), and Bulk Redirects.
