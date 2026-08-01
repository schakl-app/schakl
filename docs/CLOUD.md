# Cloud deployment (epic #199)

> The multi-org, operator-run posture of schakl. **Business-licensed**: the code that
> implements it (`apps/api/app/core/cloud/`, `apps/web/src/routes/(cloud)/`,
> `apps/web/src/lib/cloud/`) is governed by [LICENSE-COMMERCIAL.md](../LICENSE-COMMERCIAL.md),
> not the AGPL. Self-host stays the default shipping model; nothing in this document applies
> to a box without `SCHAKL_DEPLOYMENT=cloud`.

## The flag

```
SCHAKL_DEPLOYMENT=cloud        # default: self_hosted
```

One env var flips the instance posture, exactly like demo mode (#141):

| | self_hosted (default) | cloud |
|---|---|---|
| Orgs per install | one (first-run wizard) | many (provisioning API / console) |
| First-run wizard `/setup` | creates org + owner (= superuser) | creates **only the instance owner** — no org |
| Apex / base domain | unknown host (error) | the **instance console** |
| Instance admin surface | `SCHAKL_INSTANCE_ADMIN_ENABLED` (default off) | forced **on** (it is the point) |
| Instance owner → org data | allowed | **service PIN required** (below) |
| Org creation | wizard / instance admin | provisioning API (instance API key) or console |
| Cloud API surfaces | answer 404 | live |

The cloud surfaces are always in the OpenAPI spec (the generated web client is
posture-independent); at runtime every route checks the flag and answers 404 on self-host —
the same "doesn't advertise itself" behaviour as the disabled instance-admin surface.

Licensing: the provisioning surface rides the `cloud` sku's write gate (#137) — a fresh
install gets the built-in bootstrap window as its trial, after that mutations require a
license document listing `cloud`.

## Two instance principals, and only one can delegate (#26)

Operating the platform used to be one boolean. It is now two principals:

| | who | capabilities | may manage people |
|---|---|---|---|
| **owner** | `users.is_superuser` | implicitly **all** | yes |
| **admin** | a row in `instance_admins` | exactly what was granted | **no** |

Granting is **owner-only and deliberately not itself a capability**: an admin who could grant
`instance.impersonate` to themselves is an owner with extra steps, so the escalation edge does
not exist rather than being guarded. Owners may promote another owner, so this is not a bus
factor — and the last active owner cannot be demoted or revoked (`409
errors.last_instance_owner`), because a box nobody can administer is unrecoverable without
database access.

The catalog is code-defined and small (`app/core/instance/capabilities.py`): view / create /
lifecycle on orgs, read the audit trail, export data, impersonate, purge, manage API keys. The
three that reach a tenant's own contents or end it are marked *sensitive* in the console — and
on cloud each still needs the org's service PIN, so a capability is **necessary, never
sufficient**.

Manage it at **Console → Administrators** (owners only). Inviting creates the account if the
email is new; the person sets a password through forgot-password, exactly like an invited org
member. An invite with nothing ticked is valid and grants nothing — a half-finished invite must
never over-grant.

Every route on `/api/v1/instance` declares the capability it needs, and
`tests/test_instance_deny_by_default.py` makes a missing declaration a build break — the same
deny-by-default rule §15 applies to tenant routes.

**One consequence worth knowing.** `read_impersonation` runs on every request on a tenant host,
so it does not re-check capabilities there (that would be a query on the hot path). The
capability is checked when the grant is *issued*, and the grant is signed and time-boxed. So
**revoking `instance.impersonate` does not kill a session already in flight** — it lapses
within one window (`SCHAKL_IMPERSONATION_MAX_MINUTES`, ≤60). Revoking the admin, or
deactivating the account, is the immediate lever; every grant is on the audit trail either way.
A crossing that has *not* happened yet is a different matter and is re-authorized on arrival, so
withdrawing the capability does stop a link still sitting in a redirect.

### Impersonating across hosts (#288)

The grant only works alongside the administrator's own session, and both are cookies — which are
**host-scoped**. The console runs on the apex, the org runs on `<slug>.<base_domain>` or on a
domain the customer owns, so there is nothing to share and, for a customer domain, no parent to
widen a cookie to. Handing the grant over in a query string put it on a host that had a grant and
no session: the API refused before the grant could be applied and the browser landed on the
tenant's login screen.

So the crossing is an explicit, single-use **handoff**:

1. `POST /instance/orgs/{id}/impersonate` on the console host stores an `impersonation_handoffs`
   row and returns `{handoff: {host, ticket, expires_at}}` — and **no grant**. The grant JWT does
   not exist yet, so an unclaimed handoff leaves nothing usable anywhere.
2. The console sends the browser to `https://<host>/impersonate?ticket=…`. That SSR route
   redeems the ticket over `POST /instance/impersonation/claim` — the one route on the instance
   surface that answers without a session, because the whole point is that there isn't one yet.
3. The claim re-checks everything against live state (host, org still active, administrator still
   an instance principal *holding* `instance.impersonate`, service PIN still claimed, target still
   an active member), burns the ticket under `FOR UPDATE`, and returns the grant plus a session
   token for the **real administrator**, minted to expire *with* the grant. Both are set as
   httpOnly cookies on the tenant host; the operator's footprint there dies with the window.
4. Anything wrong — expired, already redeemed, wrong host, revoked capability — is one
   undifferentiated `403 errors.impersonation_handoff_invalid`, rendered as a page that says the
   link is spent, never a login redirect.

Two constraints shaped the plumbing, and both are easy to undo by accident:

- **The crossing cannot be an HTTP redirect.** Our own CSP sends `form-action 'self'` (audit F14)
  and Chrome applies it to a form submission's *whole* redirect chain, so a 303 off-origin is
  blocked before the browser asks for it. The action returns the address and the page navigates
  itself (with a plain link as the no-JavaScript fallback). Same reason **stopping** lands on the
  tenant host's own `/impersonate?stopped=1` page, which offers the link back to the console.
- **Ending it drops the minted session too.** The administrator is usually not a member of that
  org, so leaving the session behind would strand them on a 403 that looks like a login screen.

`tests/test_instance_admin.py` covers single use, host binding, expiry, revocation and the custom
domain; `apps/web/tests/e2e/cloud.spec.ts` drives it in a browser across two real hosts.

## Service PIN: tenant consent for operator access

On cloud, tenants are paying customers; the instance owner has **no standing access** to an
org's contents. The flow (default validity **24 hours**, `SCHAKL_CLOUD_SERVICE_PIN_HOURS`):

1. An org admin opens **Instellingen → Service-toegang** and generates a PIN
   (`POST /api/v1/settings/service-access`, permission `settings.service_access.manage`).
   The PIN (12 digits) is shown once; only its SHA-256 is stored. One PIN is live at a time.
2. They hand it to support out of band. The org page shows the grant's state (issued /
   claimed / expiry) and a revoke button that cuts access immediately.
3. The instance owner claims it in the console
   (`POST /api/v1/instance/orgs/{id}/service-access {pin}`). The claim binds the grant to
   **that owner**, for **that org**, until the grant expires.
4. Only then do the tenant-data endpoints answer: org detail (member list), export,
   impersonation, module changes. Until then they return `403 errors.service_pin_required`.

Lifecycle stays PIN-free by design: suspend/activate/soft-delete (and the provisioning API)
are platform decisions — billing enforcement cannot depend on tenant consent. The org list
(slug, name, status, plan) is also PIN-free: it is operations data, not tenant content.
Every step lands on the instance audit trail (`service_access.issue/revoke/unlock`).

## Provisioning API (auto-configuring new installs)

Machine surface for the operator's own billing/checkout — authenticated **only** by an
instance API key (minted in the console under *API-sleutels*, or
`POST /api/v1/instance/api-keys`; shown once; revocable; `expires_at` optional — a key can
be non-expiring). Header: `X-API-Key: schakl_…` (or `Authorization: Bearer`).

```
POST   /api/v1/instance/provisioning/orgs                 create + configure an org
GET    /api/v1/instance/provisioning/orgs                 list (slug, status, plan, url)
GET    /api/v1/instance/provisioning/orgs/{slug}          one org
PATCH  /api/v1/instance/provisioning/orgs/{slug}/plan     change plan / extend trial
POST   /api/v1/instance/provisioning/orgs/{slug}/suspend  billing-driven suspension
POST   /api/v1/instance/provisioning/orgs/{slug}/activate …and reactivation
```

Create payload: `{name, slug, owner_email, owner_password?, owner_full_name?, brand_name?,
locale?, enabled_modules?, plan?, trial_days?, email_included?, custom_domain?,
custom_domain_mode?}`. `email_included` defaults to **true** (see *Included e-mail* below). With
`owner_password` the org is fully auto-configured (the owner can log in immediately at the
returned `url`); without it the owner arrives via the forgot-password flow like an invited
member. The provisioned owner is a plain org `owner`, **never** `is_superuser` (#201).

`custom_domain` configures the customer's own domain in the same call (#292).
`custom_domain_mode` defaults to `"activate"`: **operator-asserted ownership** — the TXT
challenge is skipped (recorded on the audit trail as `domain.attach` /
`ownership: operator-asserted`), the Cloudflare custom hostname is provisioned fail-closed
in the same transaction (a Cloudflare failure rolls the whole org back — retry the call),
and the response's `dns_records` lists exactly what the customer's DNS must carry
(Type / Name / Value / TTL) so the checkout can show or mail them. The response's `url` stays
the **slug host** until the domain is live: the hostname was created moments ago and its
certificate is still being issued, and a provisioning response must hand the caller an
address that already serves (#291). It flips to the custom domain once the certificate is
active — the daily sweep or a check on the wizard notices. `"claim"` only reserves
the name and issues the challenge — the response carries the TXT card, and the org's own
admin finishes the wizard. The same two modes exist on the console and instance-admin org
pages (`PUT/GET/DELETE /api/v1/instance/orgs/{org_id}/domain`, capability
`instance.orgs.write` to change, `instance.orgs.read` to read) and as an optional field on
both org-creation forms.

### Plans

| plan | expiry | who ends it |
|---|---|---|
| `trial` | `trial_ends_at` (default `SCHAKL_CLOUD_TRIAL_DAYS` = 14; `trial_days` overrides) | the daily cron suspends it |
| `standard` | none | the billing system, over suspend/activate |
| `unlimited` | **never** | nobody — internal orgs, lifetime deals |

A trial that converts: `PATCH …/plan {"plan": "standard"}` (clears the clock). Extending a
trial: `PATCH …/plan {"plan": "trial", "trial_days": 30}`. `plan` is platform billing state
on `orgs` — unrelated to the tenant-facing `subscriptions` module. Suspension is the
existing org lifecycle: branding/login still render with an explanation; every request is
blocked with `errors.org_suspended`; data is never deleted by expiry (soft-delete + purge
remain explicit operator actions with an export gate).

## Instance console (the apex domain)

There is **no org** on the instance-management domain. On cloud, the base domain itself
(e.g. `schakl.cloud`, plus `www.`) serves the console:

- `/setup` (first run) creates the instance owner — a user with `is_superuser`, no org.
- `/console` — login, org list (status, plan, domains), org creation, per-org detail with
  PIN entry, plan control, lifecycle actions, impersonation (crosses to the org's own host over
  the single-use handoff above), instance API keys, and the instance audit trail.
- Tenant hosts never serve `/console`; the apex never serves an org.

The web app decides via `GET /api/v1/meta/instance` (`{deployment, is_instance_host,
needs_setup, base_domain}`).

## Domains & TLS (#202)

Two mechanisms, chosen per org:

1. **Subdomain (default):** `<slug>.<base_domain>` works the moment the org exists. TLS is
   the operator's **Cloudflare origin certificate** for `<base_domain>` +
   `*.<base_domain>`, mounted into Traefik (`infra/certs/origin.pem` + `origin.key`, or
   `SCHAKL_ORIGIN_CERT_DIR`) as the default certificate. Wildcard routers in
   `infra/traefik/dynamic.cloud.yml` route any subdomain; nothing per-org to do.
2. **Custom domain (CNAME + Let's Encrypt):** the org sets a domain up through the guided
   wizard under Instellingen → Branding → Eigen domein (#292): it claims the name, proves
   ownership via the DNS-TXT challenge, then points a CNAME at the target shown
   (`SCHAKL_CLOUD_CNAME_TARGET`, default `edge.<base_domain>` — give that name an A/AAAA
   record to the server). On activation the API writes `custom-domains.yml` (one router
   pair per **active** domain, each with `certResolver: letsencrypt`) into the shared
   ingress volume; Traefik watches it and issues/renews the certificate. Unverified hosts
   get no router and no certificate — the allow-list is the verified-domains table, so the
   box is never an open cert factory and never trips LE rate limits.

The fragment is rewritten on verify/clear, at API boot, and by a daily worker cron
(`SCHAKL_CLOUD_INGRESS_DIR`, set by the overlay; unset = sync off, e.g. in dev).

### Cloudflare for SaaS (the third option, #199)

An operator who fronts the instance with **Cloudflare for SaaS** replaces mechanism 2: a
verified customer domain becomes a **custom hostname** on the operator's zone, and Cloudflare
issues and renews the edge certificate. Traefik then needs no per-domain router and no ACME
resolver at all — leave `SCHAKL_CLOUD_INGRESS_DIR` unset and the catch-all routers handle
every host.

```
SCHAKL_CLOUD_CF_API_TOKEN=…        # or _FILE, pointing at a Docker secret
SCHAKL_CLOUD_CF_ZONE_ID=…
SCHAKL_CLOUD_CF_ORIGIN_SNI=        # leave empty; Enterprise-only SNI rewrite (#293)
```

**The API token needs exactly two scopes, both Zone-level, on the one zone:**

| Scope | Permission | What it is for |
|---|---|---|
| Zone → *your zone* | **SSL and Certificates → Edit** | create / read / delete custom hostnames |
| Zone → *your zone* | **DNS → Edit** | the per-org `<slug>.<base_domain>` record |

Under *Zone Resources* pick **Include → Specific zone**, never *All zones*. Nothing
account-level, no Zone Settings, and never a Global API Key. Restrict the token to the
server's egress IP and give it an expiry.

The token is **instance-level and server-side only**: it is read from the environment, never
stored in the database, never returned by an endpoint, never in the OpenAPI spec, and never
reaches the web app. Use the `*_FILE` form with a Docker secret so it does not show up in
`docker inspect`.

**What keeps Full (strict) working — and why the SNI rewrite is not it.** Cloudflare opens a
*second* TLS connection to the origin. Every custom hostname schakl creates carries a
`custom_origin_server` of the CNAME target (`edge.<base_domain>`), and Cloudflare presents that
custom origin's own name as SNI by default — which is exactly what the operator's wildcard origin
certificate covers, so Full (strict) validates. Nothing extra is needed. The HTTP `Host` header is
untouched either way, so tenant resolution still sees the customer's domain.

`SCHAKL_CLOUD_CF_ORIGIN_SNI` is a *rewrite* of that default, and **"SNI Rewrite for Custom Origin"
is an Enterprise-only entitlement** — Custom Origins themselves are available on Free, Pro and
Business, the SNI rewrite is not. So schakl sends `custom_origin_sni` **only** when the setting is
explicitly configured, and never derives it (#293): sending it on a non-Enterprise zone fails the
create with *"Access to setting a custom origin SNI has not been granted"* and leaves the
customer's domain unverified. Leave the setting empty unless you have the entitlement *and* need
an SNI that differs from the origin server; it never changes `custom_origin_server`.

Should Cloudflare refuse a call over a token scope or a plan entitlement, retrying is pointless
until the operator acts, so neither path says "try again in a moment". Provisioning an org's
subdomain answers `502 errors.cloudflare_not_entitled` rather than the retryable
`errors.cloudflare_failed`; the customer-facing domain wizard reports the same refusal as a
*failed* `cloudflare_auth` / `cloudflare_entitlement` check (see below) instead of an HTTP error,
because a 502 there would throw away the progress the customer has already made. Either way the
API log carries Cloudflare's own words plus what the operator has to change. A hostname added by
hand in SSL/TLS → Custom Hostnames is adopted by the next check (exact name match), so a manual
workaround needs no cleanup.

**Fallback origin.** Cloudflare for SaaS routes every custom hostname to one proxied record in
your zone — use the CNAME target (`edge.<base_domain>`), set under SSL/TLS → Custom Hostnames.
It must be proxied, and it must be Active before any custom hostname resolves.

### The domain wizard (#292)

Customer-side onboarding is a resumable four-step flow on `/meta/tenant/domain`:

1. **Choose** (`POST /meta/tenant/domain`) — normalize + validate the name, refuse anything
   under the base domain, check global uniqueness, issue the ownership token. The org's
   current custom domain (if any) keeps routing until the new one activates.
2. **Prove ownership** — only the `_schakl-challenge.<domain>` TXT card is shown; the
   customer is never asked to cut traffic over before control is proven.
3. **Point DNS** — after ownership succeeds the Cloudflare custom hostname is provisioned
   and the traffic CNAME card appears (the TXT card stays, flagged *temporary*).
4. **Activate & monitor** — the domain activates only when every production condition is
   ready: traffic DNS observed, hostname active, certificate issued. On self-host, ownership
   alone activates (routing is the operator's own ingress); on cloud without Cloudflare,
   ownership + observed DNS activate and Let's Encrypt takes it from there.

`POST /meta/tenant/domain/check` is the single probe-and-advance action the wizard polls.
It always answers 200 with per-layer `checks` — `ownership`, `dns_target`, `hostname`,
`certificate` — each carrying `state` (`ok` / `pending` / `failed`), a machine `code`, the
**expected** and **observed** values, and an i18n message. Conditions that plausibly mean
"still propagating" (missing TXT, NXDOMAIN, timeout) read as *pending*, never as failure —
the wizard does not declare defeat while Cloudflare validates asynchronously. `GET` reads
only persisted state (no DNS probes), which is what makes the wizard resumable across
sessions. Cloudflare is still contacted **before** the org row says anything is live, so an
edge outage leaves the claim in `routing_pending` rather than active-without-certificate.
Clearing a domain takes the opposite trade-off: the removal is best-effort, because an org
must always be able to drop its domain, and a leftover custom hostname routes nothing and
is adopted again on the next check.

Past `active` the same endpoint keeps answering, and becomes the **lifecycle** view (#291):
it re-reads the hostname and certificate, re-resolves the traffic record, and writes the
health columns `app.core.hosts.custom_domain_live` decides canonicality from. So the wizard
does not end at activation — the fourth step is where a customer sees a certificate that
stopped renewing or DNS that moved away, with the same expected/observed diagnostics.

#### Operator troubleshooting

Every non-ok check logs `domain check <correlation_id> org=<slug> …` API-side; the customer
sees the same correlation id, so a support ticket maps to the exact probe. Codes:

| code | meaning | operator action |
|---|---|---|
| `txt_missing` / `txt_nxdomain` | challenge not visible yet | usually propagation; verify the record name with the customer |
| `txt_wrong_value` | TXT exists, wrong token | customer pasted stale/partial value |
| `dns_servfail` | customer zone broken (often DNSSEC) | point customer at their DNS provider |
| `target_missing` / `target_nxdomain` | traffic CNAME absent | propagation or record not created |
| `target_wrong` | domain resolves elsewhere | another CDN/proxy in front, or wrong target |
| `hostname_pending` | Cloudflare still validating | wait; check the fallback origin is Active |
| `hostname_moved` / `hostname_blocked` / `hostname_deleted` | Cloudflare hostname state | inspect the custom hostname in the CF dashboard |
| `cloudflare_auth` | token rejected (401/403) | rotate/rescope `SCHAKL_CLOUD_CF_API_TOKEN` |
| `cloudflare_entitlement` | plan/quota refusal (#293) | raise the custom-hostnames quota / plan |
| `cloudflare_unavailable` | CF API unreachable / 5xx | transient; retries on the next check |
| `cert_pending` | certificate being issued | wait (up to ~1 h) |
| `cert_failed` | validation errors (CAA, expired token) | the check's `observed` carries Cloudflare's own validation message |

The Cloudflare API token never appears in any diagnostic, log line or response — only
Cloudflare's own error text does. Optional customer-side Cloudflare **DNS automation**
("Connect Cloudflare", #292) is deliberately not implemented: there is no
operator-independent OAuth flow to a customer's zone that meets the least-privilege bar, so
the wizard ships the precise manual record cards instead.

## Canonical host & custom-domain lifecycle (#291)

After a custom domain verifies, the org has **two valid origins**: the operator-controlled
`<slug>.<base_domain>` host and the customer's domain. Neither is removed; one is canonical.

**Verified is ownership; live is activation.** With Cloudflare for SaaS, creating the custom
hostname is not activation: the domain counts as **live** only once Cloudflare reports the
hostname `active`, its DV certificate `active`, and the routing check has not established that
the domain stopped pointing here. That state lives on `orgs` (`cf_hostname_status`,
`cf_ssl_status`, `domain_dns_ok`, `domain_cert_expires_at`, `domain_checked_at`,
`domain_check_error`) and is written in three places, all of them the same reconciliation:
activation seeds it from the custom-hostname record it already holds (the wizard's
`_activate`, and `attach` for an operator-set domain), `POST /meta/tenant/domain/check`
re-reads it whenever the wizard polls, and the daily `cloud_domains_sweep` cron (04:30) does
it unattended. The wizard's own check is deliberately the *only* customer-facing one — one
probe feeds both the per-layer diagnostics the customer reads and the columns
`custom_domain_live` consults, so the two can never disagree. Without Cloudflare (self-host,
or the
Traefik/Let's Encrypt posture) there is no state to poll: the router and certificate follow
the verification directly, so verified = live — today's behaviour, unchanged. Orgs verified
before this state existed stay live until the first sweep records the truth: an upgrade must
never silently demote a working domain.

### "Does it still point here?" — and why addresses cannot answer it

The routing check (`domainflow.routing_check`) is the one answer both the wizard's *check now*
and the sweep use. It exists in that shape because the first version of it did not: it compared
the addresses the custom domain resolves to against the edge hostname's, and **a domain fronted
by the customer's own Cloudflare publishes their zone's anycast addresses and no CNAME at all**.
That configuration — orange-to-orange, a supported Cloudflare for SaaS setup — mismatches on
every comparison no matter how correctly it is set up, which demoted healthy domains to the slug
host and mailed their owners about an outage that was not happening.

So evidence is now ranked, addresses are the weakest kind, and the first row that applies wins:

| Signal | Verdict | Why it is trusted at that strength |
|---|---|---|
| CNAME to the edge, or matching addresses | **ok** (`target_ok`) | DNS proves it outright; nothing else is fetched. |
| A fetch of `https://<domain>/api/v1/meta/domain-probe?nonce=…` that this instance answers for this org | **ok** (`target_proxied`) | End-to-end proof, through any proxy, CDN or apex flattening. The nonce is what a cache cannot echo. |
| A fetch something *else* answered — a 200 that is not ours, a stranger's 404 | **failed** (`target_wrong`) | Positive proof of the opposite: the hostname serves someone else now. |
| Cloudflare reporting the hostname `active` | **ok** (`target_edge_confirmed`) | The edge would not, if the customer's DNS had stopped reaching it. Carries the case where a WAF blocks our fetch — which outranks the row below, since serving beats record shape. |
| A visible CNAME pointing elsewhere | **failed** (`target_wrong`) | An observed wrong value; the wizard must still say *where* it went (#292). A proxied domain has no visible CNAME, so this never fires for one. |
| Nothing conclusive | **pending** (`target_unconfirmed`) | No consequence at all: `domain_dns_ok` stays `NULL`, the domain keeps serving and nobody is mailed. |

The fetch is skipped where it could only stall: the name resolving to nothing (the wizard's
most-polled state while a record propagates) and a zone answering SERVFAIL both mean nobody is
there to answer, and a connection timeout per poll is the slowest possible way to learn that.

**Only positive evidence writes `domain_dns_ok = false`.** "We could not tell" is a first-class
outcome with no side effects — a domain that is serving must never be demoted because a firewall
declined to answer us. The probe is not a security boundary: ownership is proven by the TXT
challenge long before anything is fetched, and the endpoint reveals only what the equally public
`/meta/tenant` already does. It refuses to fetch loopback/private addresses (a custom domain is
customer-controlled), follows no redirects, and stops reading at 16 KiB.

**The policy, per surface** (`app.core.hosts` is the one helper):

| Surface | Behaviour |
|---|---|
| Browser navigation | While live, top-level GET/HEAD document requests on the slug host 307 to the custom domain (`hooks.server.ts`, from `canonical_host` on `/meta/tenant`). `no-store`, never a 308 — health is state, a cached permanent redirect would brick recovery. |
| Generated links, e-mail | `org_base_url()` → the live custom domain, else the slug host. Used by e-mail branding, password/invite mails, task links. |
| OAuth / OIDC | Callback URLs derive from `org_base_url()` (`docs/SSO.md`); the runtime OIDC callback stays request-derived. While a domain is unhealthy the displayed callback flips to the slug host — matching reality, since the broken domain serves nothing. No WebAuthn surface exists today. |
| API / MCP | **Never redirected.** Both origins keep answering — a blind 307 would break non-idempotent requests and cookie-less clients. Canonical is a recommendation for API consumers, not an enforcement. |
| Instance console | Org rows carry `canonical_host` (live-aware); the impersonation jump uses it, so the operator lands on an origin that serves — which matters most exactly when the customer domain is broken. |

**Loop-safety, by construction:** only one direction ever redirects (toward `canonical_host`),
the canonical host compares equal to itself, and an unhealthy domain advertises no canonical
host at all — so at most one hop, and the slug host silently resumes serving the moment
health degrades. Sessions are host-only cookies: switching origins means signing in again,
deliberately — a customer domain must never share the base domain's cookie scope.

**Recovery:** the slug host always resolves (`resolve_org` is untouched by health), an
unhealthy domain shows a banner to holders of `settings.domain.write` instead of a generic
TLS failure, and **Instellingen → Eigen domein** — the wizard's own screen, at its *Actief*
step — shows the raw hostname/certificate/DNS state, the last error and the re-check button.
Huisstijl keeps only a summary card linking there: one screen owns the domain, setup and
lifecycle alike.

### Certificate renewal, HTTP DCV and Delegated DCV

The custom hostnames schakl creates are **exact, non-wildcard** names validated with
`ssl.method=http`. Cloudflare renews their DV certificates through the same automatic HTTP
DCV **as long as the hostname stays `active` and keeps resolving to the SaaS target** — the
customer does not need Cloudflare as their DNS provider and does not need to proxy anything
in their own zone; the CNAME routes their traffic through the schakl edge, which answers the
renewal challenge itself. They *may* proxy it (orange-to-orange), and the routing check above
is what keeps that supported rather than merely tolerated. Renewal breaks when the domain stops
pointing at the target or a CAA record blocks the CA — which is exactly what the
sweep watches: it re-reads every hostname's status/SSL state, runs the routing check, and
mails the org's administrators **once per distinct problem** (`orgs.domain_alerted_for`
fingerprint) — on any not-live state, and ahead of an expiry closer than 15 days (Cloudflare
renews ~30 days out, so 15 means renewal has been failing for weeks).

**The alert carries the diagnosis, not just the verdict** (`cloud/domain_alert.py`). The
reconciliation returns the per-layer `DomainCheck`s it decided with, so the mail lists the
records the domain needs, the value each must hold, what DNS answers instead, and each failing
layer's own explanation — in the same `settings.domain.*` catalog strings the settings screen
renders, because a mail that phrased the problem its own way would be a second implementation
of the diagnosis. Its links point at the **slug** host: the custom domain is exactly what may
not be answering.

**Who is told: administrators, and only them.** Recipients hold `settings.domain.write` (or
the owner wildcard) — nobody is mailed about infrastructure they cannot change — *and* are
staff: a `client`-role or contact-linked portal account is never a recipient even if a
misconfigured role grants it the permission (#274's definition of an external login). The
mail names its own recipients, so an admin knows whether a colleague already has it. The
fingerprint is recorded **only when a mail actually went out**; an org whose administrators
are all inactive, or whose transport is down, is alerted again tomorrow instead of having its
outage silently marked as handled.

**Delegated DCV is deliberately deferred.** It would let certificates renew even while the
domain points elsewhere, at the cost of every customer adding a permanent `_acme-challenge`
CNAME (and conflict-checking any existing `_acme-challenge` TXT). For exact hostnames that
actually point at the platform, HTTP DCV renews unattended — and when it can't, the sweep
says so before browsers do. Revisit alongside the guided setup wizard (#292), which will
consume the same state this lifecycle work records. Cloudflare webhooks are likewise deferred
(account-level configuration; the daily sweep is the safety net).

## Automatic subdomain provisioning (#199)

With Cloudflare configured, creating an org also creates its address:

1. the slug is validated and checked for global uniqueness, as before;
2. **reserved names are refused** — `edge` is the fallback origin every custom hostname routes
   through and `console` is the instance console, so an org taking either breaks the instance
   rather than only itself;
3. the zone is checked for an existing record at `<slug>.<base_domain>`; one that already
   exists answers `409 errors.subdomain_taken`. A wildcard `*.<base_domain>` never matches
   this check — it is stored under its own literal name, so a catch-all does not read as
   "every name taken";
4. a **proxied CNAME** to the CNAME target is created and its id stored on the org.

Failure is fail-closed: the org is not created. A provisioned org that does not resolve is
worse than a provisioning call the billing system can retry. Re-slugging an org moves the
record (new one first, then the old is dropped), so a failure halfway leaves the org reachable
at its current address rather than at none.

## Per-org end date and termination (#199)

An org may carry an **`ends_at`**. `NULL` means unlimited, which is the default for every org
and the only value any existing install has after upgrading — nothing below can happen by
accident.

| stage | the tenant experiences | recoverable |
|---|---|---|
| `active` | nothing; before `ends_at` | — |
| `warning` | full access, plus a banner and an e-mail (`grace_days`, default 14) | yes |
| `suspended` | login renders, every request refused (`retention_days`, default 30) | yes |
| terminated | the org and its data are gone | from the archive |

The suspended window is deliberate: it is the last state from which a wrong date or a customer
who paid late is fixable by flipping one field.

```
SCHAKL_CLOUD_GRACE_DAYS=14
SCHAKL_CLOUD_RETENTION_DAYS=30
SCHAKL_CLOUD_LIFECYCLE_ENABLED=false        # the sweep runs at all
SCHAKL_CLOUD_LIFECYCLE_DESTRUCTIVE=false    # …and may actually purge
SCHAKL_CLOUD_LIFECYCLE_BATCH=25             # orgs terminated per run
```

**Two switches, because the last step cannot be undone.** Deploy
`ENABLED=true, DESTRUCTIVE=false` first: real warnings and suspensions, and terminations that
stop after archiving, so the dates and the copy can be checked against live orgs while
everything is still recoverable.

Set the date from the console (Organizations → an org → *End date*) or from the billing system
over `PATCH /api/v1/instance/provisioning/orgs/{slug}/lifecycle`. It is separate from `plan` on
purpose: a plan says *how* an org is billed, an end date says *until when* it exists. Both are
PIN-free — billing enforcement cannot depend on tenant consent. Moving the date forward or
clearing it re-arms the stage, so a renewed customer stops being warned and is warned again
next time.

The daily `cloud_lifecycle_sweep` cron (04:00, after the trial sweep) advances each org.
Termination is ordered so every failure mode is safe:

1. mark deleted, so the archive is taken against frozen data and `purge_org`'s
   export-since-soft-delete precondition is satisfied honestly rather than bypassed;
2. **archive rows and bytes** to `archive/<org_id>/<timestamp>.zip` in the storage backend,
   outside every org's key space;
3. remove the Cloudflare custom hostname and subdomain record;
4. delete the org's stored bytes (`delete_prefix`);
5. purge the rows.

Anything that raises stops the sequence before step 5 and the next sweep retries; a
soft-deleted org resolves for nobody, so retrying is inert. **A failed archive never purges.**

`GET /api/v1/instance/orgs/{id}/archive` is the same complete archive on demand — what an
agency leaving should take. `/export` remains rows-only, which is a pointer-shaped answer once
files live in object storage, so prefer the archive. Importing one restores the bytes and
**re-keys them onto the new org**: a `files` row carries `<org_id>/<file_id>`, and copying it
verbatim left the new org reading out of the source org's prefix.

## Included e-mail vs bring-your-own

Instance-defined choice (`SCHAKL_INSTANCE_EMAIL_*`, see `infra/compose.cloud.yaml`; also
usable on self-host):

- **Instance e-mail off** (default): exactly today's behaviour — every org configures its
  own transport (#17) or e-mail is off.
- **Instance e-mail on:** an org without its own transport automatically sends through the
  instance transport (from the instance's own address — SPF/DKIM belong to the operator's
  domain — displayed as the org's brand name), and Instellingen → E-mail offers the
  explicit choice: *included e-mail* (`provider="instance"`, stores only from-name and
  reply-to) or any bring-your-own provider, exactly as before.

**Per org, not per instance.** `orgs.email_included` (default **true**) decides whether a
given org may use the operator's transport at all — an entitlement beside `plan`, written
only from the instance surface: the `email_included` field on org creation (both org-creation
forms and `POST /instance/provisioning/orgs`, ticked/true by default) and the toggle on the
console / instance-admin org page (`PATCH /instance/orgs/{org_id}`, a partial update, so a
rename never carries an entitlement change). False makes the org exactly as unconfigured as
one on a box with no instance transport: no fallback, the choice is refused (`409
errors.instance_email_unavailable`), and a *stored* `provider="instance"` row stops sending
rather than being rerouted. Default true because an operator who never touches the field
must not provision orgs that silently cannot mail.

**The tenant's screen says which transport is live.** Included e-mail stores nothing, so
Instellingen → E-mail used to read "not configured" over a blank SMTP form while mail was
leaving happily. `GET /settings/email` now always returns an object with `active_provider`,
`active_from_email`, `active_from_name` and this org's `instance_email_available`; the page
states the active transport and the address it sends as, offers a test send, and opens the
form on what is actually sending. See `docs/EMAIL.md` → *Which transport sends*.

Google Workspace and LLM providers deliberately stay **bring-your-own-keys per org** on
cloud — no platform-owned OAuth broker (that remains its own issue, #203) and no shared AI
credential.

## Capacity (from #205, short form)

Target ≤ 10 orgs on one server (~150 staff, 20–40 concurrent): CCX33 (8 vCPU / 32 GB)
comfortable, CCX23 (4 vCPU / 16 GB) floor. Mandate encrypted DB backups + tested restore
(no HA on one box), ARQ concurrency caps and statement timeouts against noisy neighbours.
web/api/worker are stateless — scale-out is additive (managed Postgres/Redis + more app
nodes), not a rewrite.

## Ops quick-start

```bash
# 1. DNS: <base_domain> A record + *.<base_domain> (via Cloudflare), edge.<base_domain> A record
# 2. Cloudflare origin cert for <base_domain> + *.<base_domain> → infra/certs/origin.{pem,key}
# 3. .env: SCHAKL_BASE_DOMAIN, SCHAKL_ACME_EMAIL, SCHAKL_SECRET_KEY, POSTGRES_*, and
#    optionally the SCHAKL_INSTANCE_EMAIL_* block
docker compose -f infra/compose.yaml -f infra/compose.cloud.yaml up -d
# 4. https://<base_domain>/setup → create the instance owner
# 5. Console → API-sleutels → mint a provisioning key for your billing system
```
