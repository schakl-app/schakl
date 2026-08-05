# Google Workspace integration — design notes (P3)

> Calendar, Drive, and Gmail look like one "Google integration" but they are **two token
> systems and three different data problems**. Getting the boundaries right up front is what
> keeps P3 (§10) from becoming a sync swamp. Read this before building any Google surface.
> Status: **shipped** (issue #22) — one licensed registry module `google` (sku `"google"`)
> holding the core plus `calendar/`, `drive/` and `gmail/` subpackages, with the touchpoint
> timeline in the free `interactions` module (contactmomenten). Deviations from the letter of
> this doc, decided during the build: one registry module instead of four (one license, one
> enablement, boundaries kept as subpackages); OAuth client credentials live per-org in the DB
> (Instellingen → Google, the #76 SSO pattern) with the env vars as fallback; our own
> browse-and-pick UI instead of the Google Picker; matched emails land **pending** and only
> the mailbox owner may approve/reject/remap (body fetched after approval).

## The one rule

**Login is not API access.** OIDC "Sign in with Google" (authentication) and Workspace API
access (Calendar/Drive/Gmail authorization) are separate grants with separate lifetimes.
Never make the login token carry API scopes. Build them as two flows that a nice UX *links*,
not one flow that does both.

## 1. Login ≠ API access

CLAUDE.md §3 already lists these separately ("Authlib OIDC relying-party" vs "Google OAuth
for Workspace scopes"). Keep them separate in code too:

| | OIDC login (authentication) | Workspace API access (authorization) |
|---|---|---|
| Scopes | `openid email profile` | `calendar.events`, `drive`/`drive.readonly`, `gmail.readonly`, … |
| Token | short-lived ID token, used at login | **refresh token stored server-side**, used indefinitely |
| Level | org-enforced (§P0: enabling OIDC disables local login) | per-user *or* domain-wide |
| Consent | "Sign in with Google" | "Connect your Google account" — separate, incremental |

Asking for Gmail/Drive scopes on the login screen is alarming UX and technically wrong: API
access needs `access_type=offline` + a stored refresh token, which login doesn't produce.
Even when a tenant enforces Google OIDC login, "connect Google for Calendar/Drive/Gmail" is a
distinct step.

**The bridge:** Google supports **incremental authorization**. A user who logged in via
Google can be walked up to Workspace scopes with a one-click "connect" that adds scopes to the
existing grant and returns the refresh token. It *feels* unified while staying two grants.

## 2. Self-hosting is a gift — lean into it

Each agency **self-hosts and registers its own Google Cloud project + OAuth client**, marked
**"Internal"** on the consent screen (same Workspace domain). That **skips Google's
verification and CASA security assessment** for restricted scopes (Gmail read, full Drive) —
the assessment that normally makes those scopes impractical for a SaaS. Our deployment model
(§5, "build multi-tenant, deploy single-tenant") sidesteps it entirely.

Consequences:
- Each install supplies its own OAuth client credentials in config
  (`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` per deployment) — a per-install secret, never
  hardcoded, never shared across installs.
- One Workspace domain per install makes **domain-wide delegation** (a service account
  impersonating any user in the domain) a legitimate option, not just per-user OAuth.

**Recommendation:** default to **per-user OAuth** (less privileged, standard, works even for a
personal Gmail), and support **domain-wide delegation as a tenant setting** for agencies that
want zero-friction, survives-forever central access for sync/automation. Build the abstraction
so callers ask *"give me a Google client acting as user X"* and the integration layer resolves
it — DWD if configured, else that user's stored token. Never let a caller reach for a raw
token.

## 3. Architecture: one `google` core + three surface modules

Follows the module pattern (§6).

- **`google` (core integration)** — owns the OAuth flows, the **encrypted token vault**, DWD
  config, and the client factory. `google_connections(org_id, user_id, google_sub, email,
  scopes[], refresh_token_enc, access_token_enc, expiry, ...)`. **Encrypt refresh tokens at
  rest** (envelope encryption). Handles revocation, re-consent, and incremental scope upgrades.
- **`google.calendar`, `google.drive`, `google.gmail`** — each contributes, via the registry:
  its scopes to the consent, its ARQ cron jobs (watch renewal, polling), its company/project
  panels, and its webhook routes.

Keep `org_id` on every table even though we deploy single-tenant (§5). Webhooks map the
incoming channel/notification back to org + connection via our own channel token.

## 4. Calendar

- Use **push notifications (`watch` channels) + `syncToken`** for incremental sync, not
  polling — pull only deltas (matches `docs/PERFORMANCE.md`).
- `calendar_event_links(org_id, local_type, local_id, google_event_id, calendar_id, etag)`.
- Watch channels expire (~weeks) → renew with an **ARQ cron job** (§6 `cron_jobs`).
- Start **one-way** where it's cheap: §14 already wants approved leave → Google Calendar.
  Two-way is much harder — don't sign up for it in v1.

## 5. Drive — use it directly; do NOT put object storage in front

**Reference/link model, no sync, no mirror.** The Shared Drive is already the source of truth.
Mirroring to object storage is a permanent two-way-sync bug factory (renames, moves, conflict
resolution, permission replication, duplicate storage) for no benefit in an internal tool
where everyone already has Drive access. We have no object store in the stack today (§3) —
don't add one for this.

Instead:
- `drive_links(org_id, entity_type, entity_id, drive_file_id, drive_url, name, mime, is_folder,
  shared_drive_id)`.
- Render an embedded file browser at view time via the Drive API scoped to the folder / shared
  drive. Cache listings briefly in Redis for snappiness, but **Drive stays authoritative**.
- **Automation via the event bus (§6):** subscribe to `company.created` → create the client's
  Shared Drive folder from a template → store the link. Same for projects.

**Scope tradeoff:** browsing *existing* client folders needs `drive.readonly` or `drive`
(the narrow `drive.file` only sees files the app itself created). That's a restricted scope —
fine under the "Internal" OAuth app above, but note it.

*When would object storage in front be right?* Only for offline access, full-text indexing of
file contents, or serving files to people without Drive accounts. None apply to an internal
agency tool — so, direct.

## 6. Gmail — trickiest, most privacy-sensitive

**Do not sync whole mailboxes.** Start with **matched, metadata-first logging**:

- Only link emails whose participants match a known `contact`; attach to the company/project
  timeline.
- Store **metadata + a deep link** (`message-id`, `thread-id`, subject, snippet, participants,
  timestamp, `https://mail.google.com/mail/u/0/#all/<msgid>`) rather than full bodies by
  default. Pull the body on demand — lighter, faster, far less invasive.
- **Ingestion:** Gmail's real-time `watch` requires **Google Pub/Sub** (unlike Calendar) —
  extra infra. Start with **periodic ARQ polling using `historyId`** (incremental, cheap); add
  Pub/Sub push later only if latency demands it.
- A dedicated `email_logs` module (or generic `relations` rows, §6) attaching to
  company/contact/project, with its own company panel.
- **Privacy:** mailbox connection is per-user and opt-in; let users scope it to a label/query.
  "The CRM reads all my email" is a trust landmine even internally.

## 7. Marketing sources — enabled APIs are a *project* fact, not a token fact

The `marketing` module (#134) rides this same OAuth client, so it adds no login and no second
grant. What it does add is a dependency the other surfaces don't have: **each source calls one
or two Google APIs that must be switched on in the Cloud project the OAuth client belongs to.**

| Source | Scope | APIs to enable in the Cloud project |
|--------|-------|--------------------------------------|
| GA4    | `analytics.readonly` | **Google Analytics Admin API** (the property picker, `analyticsadmin.googleapis.com`) **and** **Google Analytics Data API** (every metric, `analyticsdata.googleapis.com`) |
| Search Console | `webmasters.readonly` | Google Search Console API |
| Google Ads | `adwords` | Google Ads API, **plus** a per-org developer token (§ the module's own settings) |

Two APIs for GA4 is the trap: enabling only the Data API leaves the picker dead while the rest
of the module looks configured, because listing properties is an *Admin* API call.

### One consent for the module, not one per source

`/google/oauth/connect?include_marketing=1` asks for all three marketing scopes in a single
consent. The per-source flags (`include_analytics`, `include_search_console`, `include_ads`)
still exist for an API caller that wants exactly one, but **no UI uses them**: each picker used
to link its own flag, so switching on one module cost three separate walks through Google's
consent screen. Incremental authorization does not save that round-trip — it only makes the
second and third look necessary. Instellingen → Account asks for the same union when the
marketing module is on and the person may manage links, so connecting from there is enough too.

The connect link also carries `next=` — the page the consent was started from, so a picker on a
client's page lands back on that client's page. Only a **site-relative** path is honoured
(`oauth.safe_return_path`): it comes off a URL, so `//host`, `/\host`, an absolute URL and a
newline are all discarded in favour of `/settings/account`. It rides the session across the
round-trip, because Google echoes neither the OAuth state's contents nor a redirect URI that
varies per page (the redirect URI is an exact-match allowlist entry in the Cloud console).

### A link belongs to a person, and every screen says so

Marketing links sync through **one colleague's** connection. `LinkRead.connection_owner` and
`SourceMetrics.connection_owner` carry that person (their name, the Google account, and whether
they are the caller), and the panel, the tab and the picker render it. Without it, the second
employee sees a working link with no hint whose it is — connects Google again for data that is
already flowing — and nobody learns that the client's numbers stop the day that person leaves.
For the same reason `AccountsResponse.connected_via` names colleagues whose grant already
reaches the source the picker is empty for.

### Google Ads manager accounts (MCC) are the normal agency shape

Access is granted to the **manager**, not to each client under it, and
`customers:listAccessibleCustomers` answers *direct* grants only — so the raw list is one MCC id
and the picker offers none of the accounts the agency actually runs. `GAdsAdapter.list_accounts`
therefore reads `customer.manager` for each accessible customer and, for a manager, expands it
with a single `customer_client` query — which returns the **whole hierarchy**, so a nested MCC
needs no recursion. Sub-managers are dropped and their clients kept: every account in the tree is
reachable with the top-level manager as `login-customer-id`.

Each child is stored with `config["manager_id"]`, and `_headers` turns that into the
`login-customer-id` header on every later call (metrics, drill-downs, the nightly sync). Without
it those calls are made by a user with no direct grant on the account and 403. Manager accounts
are never offered as options — Google refuses metric queries against one, so linking it would
produce a permanently erroring link rather than a roll-up. The expansion is capped at
`MAX_MANAGER_CHILDREN` and going over it is **logged**, because a picker showing 500 of an
agency's 900 accounts is indistinguishable from an agency with 500.

An Ads link created before this shipped carries no `manager_id`; remove it and pick the account
again (the picker hides accounts already linked to the client, so the removal is what returns it
to the list).

### The Ads API version is a setting, because it expires

Google Ads is the one Google API here whose **URL** carries a version, and each version is
sunset roughly a year after release; from that day every path under it answers **404**. That is
not a credential, scope or account failure, so none of the picker's teaching states fit it and
the module simply looks broken (v18 sunset 2025-08-20 and did exactly this). The release pins a
current version in `marketing.sources.gads.DEFAULT_API_VERSION`, and
`SCHAKL_GOOGLE_ADS_API_VERSION` overrides it — an install that outlives its release can be
bumped from the compose file instead of waiting for one.

**A disabled API and a dead grant both come back 403.** They are not the same failure and they
do not have the same cure: a disabled API is refused before the token is even considered, so
reconnecting mints an identical token against the same project and fails identically. Google
says which is which in the response *body*, and `httpx` throws it away —
`str(HTTPStatusError)` is the status line and the URL, nothing more. So every Google call site
runs its exception through `google.client.describe_api_error`, which lifts out
`error.details[].reason` (`SERVICE_DISABLED` → the API is off, `ACCESS_TOKEN_SCOPE_INSUFFICIENT`
→ the grant is short) and Google's own message — **which names the Cloud project number**.

One classifier, `marketing.service._failure_key`, turns that into the message, so the picker,
the drill-down and the nightly sync can never disagree about what a given 403 meant:

| Google's reason | Message | Reconnect offered? |
|-----------------|---------|--------------------|
| `SERVICE_DISABLED` / `accessNotConfigured` | `marketing.api_not_enabled` | **No** — it mints the same token against the same project and fails identically |
| `ACCESS_TOKEN_SCOPE_INSUFFICIENT` (and friends) | `marketing.scope_insufficient` | Yes — this is the one case reconnecting genuinely cures |
| a **404 from Google Ads** (and only Ads) | `marketing.ads_api_version` | **No** — the version in the URL is sunset; upgrade or set `SCHAKL_GOOGLE_ADS_API_VERSION` |
| anything Google didn't name | the caller's fallback (`marketing.accounts_error`; the sync keeps Google's own sentence) | Yes |

A scope-short 403 on the account picker also comes back as `has_scope=false`, because that is
precisely what the field means — so the picker renders the "reconnect to grant access to this
source" branch it already had, rather than a generic error with a reconnect link bolted on.

**Which project, though?** `client_credentials` falls back to the instance-wide
`SCHAKL_GOOGLE_CLIENT_ID` whenever an org has not filled in Instellingen → Google. That org is
then talking to Google as *the instance operator's* Cloud project, and an admin who enables the
Analytics APIs in their own project changes nothing. Nothing else in the product makes this
visible — the connect flow, the consent screen and the granted scopes all look right — so the
failure log names the client in use (`org client …` vs `instance env client …`; a client id is
public and its leading digits *are* the project number). Expect this on a **cloud** instance in
particular, where instance-wide credentials are the norm and a per-org client is the exception.

## Suggested build order within P3

1. `google` core: OAuth connect flow + encrypted token vault + client factory (per-user first).
2. Calendar (watch + syncToken + cron renewal) — proves the webhook + cron plumbing.
3. Drive links + template-folder-on-`company.created` — high value, low risk, showcases the
   event bus.
4. Gmail matched logging (poll-based) — last: heaviest on privacy and infra.
5. Domain-wide delegation as an optional tenant setting once per-user works.

## Checklist for any Google surface

- [ ] Login and API access are separate grants; login token never carries API scopes.
- [ ] Refresh tokens encrypted at rest; revocation and re-consent handled.
- [ ] Client obtained via the "act as user X" factory, never a raw token.
- [ ] Incremental sync (syncToken/historyId), not full polling.
- [ ] Watch channels renewed by an ARQ cron job.
- [ ] `org_id` on every table; webhook maps back to org + connection.
- [ ] Minimum scopes requested; restricted scopes justified by the "Internal" app model.
- [ ] The APIs the surface calls are listed in §7, and a failure reports Google's own reason
      (`describe_api_error`) plus the OAuth client in use — never a bare status line.
