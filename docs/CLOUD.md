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
