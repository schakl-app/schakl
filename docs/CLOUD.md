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
locale?, enabled_modules?, plan?, trial_days?}`. With `owner_password` the org is fully
auto-configured (the owner can log in immediately at the returned `url`); without it the
owner arrives via the forgot-password flow like an invited member. The provisioned owner is
a plain org `owner`, **never** `is_superuser` (#201).

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
  PIN entry, plan control, lifecycle actions, impersonation (jumps to the org's own host),
  instance API keys, and the instance audit trail.
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
2. **Custom domain (CNAME + Let's Encrypt):** the org claims a domain under Instellingen →
   Branding, points a CNAME at the target shown there (`SCHAKL_CLOUD_CNAME_TARGET`,
   default `edge.<base_domain>` — give that name an A/AAAA record to the server), proves
   ownership via the existing DNS-TXT challenge, and verifies. On verification the API
   writes `custom-domains.yml` (one router pair per **verified** domain, each with
   `certResolver: letsencrypt`) into the shared ingress volume; Traefik watches it and
   issues/renews the certificate. Unverified hosts get no router and no certificate —
   the allow-list is the verified-domains table, so the box is never an open cert factory
   and never trips LE rate limits.

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

Should Cloudflare refuse a call over a token scope or a plan entitlement, it answers
`502 errors.cloudflare_not_entitled` rather than the retryable `errors.cloudflare_failed`, and the
API log carries Cloudflare's own words plus what the operator has to change — retrying is
pointless until they do. A hostname added by hand in SSL/TLS → Custom Hostnames is adopted by the
next verify (exact name match), so a manual workaround needs no cleanup.

**Fallback origin.** Cloudflare for SaaS routes every custom hostname to one proxied record in
your zone — use the CNAME target (`edge.<base_domain>`), set under SSL/TLS → Custom Hostnames.
It must be proxied, and it must be Active before any custom hostname resolves.

Verification order on `POST /meta/tenant/domain/verify`: the DNS TXT challenge, then global
uniqueness, then **Cloudflare, before the org row is touched**. A Cloudflare outage therefore
leaves the domain *unverified* (`502 errors.cloudflare_failed`) rather than verified with no
certificate behind it. Clearing a domain takes the opposite trade-off: the removal is
best-effort, because an org must always be able to drop its domain, and a leftover custom
hostname routes nothing and is adopted again on the next verify.

## Canonical host & custom-domain lifecycle (#291)

After a custom domain verifies, the org has **two valid origins**: the operator-controlled
`<slug>.<base_domain>` host and the customer's domain. Neither is removed; one is canonical.

**Verified is ownership; live is activation.** With Cloudflare for SaaS, creating the custom
hostname is not activation: the domain counts as **live** only once Cloudflare reports the
hostname `active`, its DV certificate `active`, and the DNS drift check still sees the domain
pointing at the SaaS target. That state lives on `orgs` (`cf_hostname_status`,
`cf_ssl_status`, `domain_dns_ok`, `domain_cert_expires_at`, `domain_checked_at`,
`domain_check_error`) and is written by the verify flow (seed), by
`POST /meta/tenant/domain/check` (the settings page's *Status controleren* button) and by the
daily `cloud_domains_sweep` cron (04:30). Without Cloudflare (self-host, or the
Traefik/Let's Encrypt posture) there is no state to poll: the router and certificate follow
the verification directly, so verified = live — today's behaviour, unchanged. Orgs verified
before this state existed stay live until the first sweep records the truth: an upgrade must
never silently demote a working domain.

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
TLS failure, and Instellingen → Huisstijl shows the raw hostname/certificate/DNS state, the
last error and a re-check button.

### Certificate renewal, HTTP DCV and Delegated DCV

The custom hostnames schakl creates are **exact, non-wildcard** names validated with
`ssl.method=http`. Cloudflare renews their DV certificates through the same automatic HTTP
DCV **as long as the hostname stays `active` and keeps resolving to the SaaS target** — the
customer does not need Cloudflare as their DNS provider and does not need to proxy anything
in their own zone; the CNAME routes their traffic through the schakl edge, which answers the
renewal challenge itself. Renewal breaks when the domain stops pointing at the target,
another CDN sits in front of it, or a CAA record blocks the CA — which is exactly what the
sweep watches: it re-reads every hostname's status/SSL state, runs the DNS drift check, and
mails the org's domain managers **once per distinct problem** (`orgs.domain_alerted_for`
fingerprint) — on any not-live state, and ahead of an expiry closer than 15 days (Cloudflare
renews ~30 days out, so 15 means renewal has been failing for weeks).

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
