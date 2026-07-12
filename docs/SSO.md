# Single sign-on (OIDC) — setup & the redirect URI

> How to point an external identity provider (Google, Entra ID, Authentik, Keycloak, …) at a
> schakl install, and how to get the **redirect URI** right — the one thing that trips up every
> first setup. Since issue #76 SSO is configured **in the app, per organization** — Instellingen
> → Single sign-on — not with environment variables. Design boundaries for *Google Workspace*
> (login vs API access) live in [`GOOGLE.md`](GOOGLE.md).

## Where SSO is configured

**Instellingen → Single sign-on** (permission: `settings.auth.manage`; admins and owners hold
it by default). The page holds everything the flow needs:

- **Discovery URL** — the IdP's `…/.well-known/openid-configuration` address.
- **Client ID** and **client secret** — from the app registration at the IdP. The secret is
  stored **encrypted at rest** (key derived from `SCHAKL_ENCRYPTION_KEY`, falling back to
  `SCHAKL_SECRET_KEY`) and is **write-only**: the API accepts a new value and only ever reports
  "configured / not configured". Leave the field empty on later saves to keep the stored one.
  If the secret key is still the shipped default, the page warns — set a real
  `SCHAKL_SECRET_KEY` before storing a client secret in production.
- **Display name** — what the login button says ("Inloggen met &lt;name&gt;").
- **Role for new users** + **auto-provision** — whether a first SSO login creates a
  membership, and with which role. With auto-provision off, only people who already have
  access can sign in through SSO.
- **Callback URL** — displayed read-only, derived from the org's verified custom domain (or
  `<slug>.<base_domain>`). Copy it into the IdP; see below for why it must match exactly.
- **Test connection** — fetches and validates the discovery document server-side.
- **Enable** shows the SSO button; **Require single sign-on** (enforce) turns password login
  off for the org.

Changes apply immediately — no restart, no redeploy. The old `SCHAKL_OIDC_*` env vars are
retired: the #76 migration read them once at upgrade time and seeded each org's row from them
(secret already encrypted); after that release they are ignored and can be removed from the
compose file.

## The bootstrap order (no chicken-and-egg)

A fresh install never needs SSO to exist first: `/setup` always creates a **local** owner
account. The owner then signs in with a password, configures SSO in Instellingen, runs **Test
connection**, tries the SSO button, and only then flips **Require single sign-on**. Enforcement
is a post-setup, per-org setting — the boot-time deadlock of issue #75 no longer exists.

## Never locked out: the two guardrails

1. **Enforce requires a successful test.** The API refuses to store `enforced` until the
   *current* connection fields (discovery URL, client id, secret) have passed a Test
   connection; changing any of them clears that marker and demands a new test. Enforcing an
   untested config would kill password login with nothing proven to replace it.
2. **Break-glass:** `SCHAKL_FORCE_LOCAL_LOGIN=true` (environment, API container) re-enables
   local password login regardless of any org's enforce setting. When the IdP is down,
   misconfigured, or the enforce toggle was flipped in error: set the variable, restart the
   API, sign in locally, fix or disable SSO in Instellingen → Single sign-on, then unset it.

## The redirect URI is fixed by the code

Every OIDC provider asks you to register one or more **authorized redirect URIs** (a.k.a.
callback URL, reply URL). For schakl it is always, exactly:

```
https://<your-host>/api/v1/auth/oidc/callback
```

- **`https`** — never `http` (see the scheme rule below). Public providers reject `http://`
  for anything but `localhost`.
- **`<your-host>`** — the exact host users reach the app on: the org's verified custom domain
  (e.g. `breik.cloud`) or `<slug>.<base_domain>`. No `www.` unless that is literally the host.
- **`/api/v1/auth/oidc/callback`** — fixed. The route is `GET /callback`
  ([`core/auth/oidc.py`](../apps/api/app/core/auth/oidc.py)), mounted at `/auth/oidc`
  ([`core/auth/router.py`](../apps/api/app/core/auth/router.py)) under the `/api/v1` prefix
  ([`app/main.py`](../apps/api/app/main.py)). No trailing slash.

Providers match this string **exactly** — scheme, host, port, and path all have to agree.
`https://host/api/v1/auth/oidc/callback` and `https://host/api/v1/auth/oidc/callback/`
are two different URIs to Google.

The settings page displays this exact URL (built from the org's domain) so you can copy it.
At runtime the app **builds** it from the request (`request.url_for("oidc_callback")`) — there
is no env var that overrides it. So the host and scheme the app *sees* are what get sent to
the provider. Getting those right is entirely about the reverse proxy (next section).

## The scheme rule: the app must know it is behind HTTPS

TLS is terminated upstream (Cloudflare / Traefik) and the app is reached over plain `http`
on the internal network. Unless the app is told to trust the proxy, `request.url_for(...)`
uses the **internal** scheme (`http`) and emits `http://<host>/api/v1/auth/oidc/callback` —
which no public provider will accept, and which will never match the `https` URI you
registered. This produces a `redirect_uri_mismatch` (see troubleshooting).

Two things must be true in production:

1. **Uvicorn trusts the proxy's forwarded headers.** The API is started with
   `--proxy-headers --forwarded-allow-ips="*"` so `X-Forwarded-Proto: https` is honored and
   the generated scheme becomes `https`. (`*` is acceptable because only the reverse proxy can
   reach the API container's port; restrict it to the proxy's subnet if you want to be strict.)
2. **The public request is actually HTTPS.** The browser must reach the site over `https`
   (e.g. Cloudflare "Always Use HTTPS"), so the proxy sends `X-Forwarded-Proto: https` in the
   first place. If a user loads the site over `http`, the app will faithfully echo `http`.

Also set `SCHAKL_AUTH_COOKIE_SECURE=true` in production so the session cookie is only sent over
HTTPS. It is not what causes the mismatch, but it belongs to the same "we are behind TLS" story.

## Provider setup

### Any OIDC provider (Authentik, Keycloak, Entra ID, Auth0, …)

1. Register an application / OAuth client of type **web / confidential** (it has a client
   secret and does a server-side code exchange).
2. Set the **redirect URI** to the callback URL shown on Instellingen → Single sign-on
   (`https://<your-host>/api/v1/auth/oidc/callback`).
3. Grant the scopes `openid email profile` (schakl requests exactly these; `email` is required —
   the callback rejects a login with no email).
4. Enter the **discovery URL** (`…/.well-known/openid-configuration`), the **client id** and
   the **client secret** on Instellingen → Single sign-on, save, and run **Test connection**.

### Google as the login provider

Using "Sign in with Google" as the OIDC IdP (distinct from Google *Workspace API* access — see
[`GOOGLE.md`](GOOGLE.md), the two are separate grants):

1. [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services → Credentials**.
2. **Create credentials → OAuth client ID → Web application** (or open the existing client).
3. Under **Authorized redirect URIs**, add exactly:
   ```
   https://<your-host>/api/v1/auth/oidc/callback
   ```
   For a Workspace-only install, mark the OAuth consent screen **Internal** — it keeps the
   client to your own domain and avoids Google's verification for the login scopes.
4. You do **not** need an "Authorized JavaScript origin" — this is a server-side redirect flow,
   not a browser (GIS/implicit) one.
5. On Instellingen → Single sign-on, set the discovery URL to
   `https://accounts.google.com/.well-known/openid-configuration`, enter the client id
   (`….apps.googleusercontent.com`) and secret, save, and run **Test connection**.

Changes to Google redirect URIs can take a few minutes to propagate.

## Troubleshooting

### `redirect_uri_mismatch` (the `http://` case)

**Symptom.** Google shows *"Je kunt niet inloggen … redirect_uri_mismatch"* with
`Details van verzoek: redirect_uri=http://<your-host>/api/v1/auth/oidc/callback`.

**Cause.** The app sent an **`http`** redirect URI. Google rejects `http` for public domains,
and it never matches the `https` URI you registered. The app emitted `http` because it does not
trust the proxy's `X-Forwarded-Proto` (see "The scheme rule" above).

**Fix.**
1. Register `https://<your-host>/api/v1/auth/oidc/callback` in the provider (not `http`).
2. Start the API with `--proxy-headers --forwarded-allow-ips="*"` so it emits `https`.
3. Ensure the public request is HTTPS (Cloudflare "Always Use HTTPS").
4. Re-check the error page: the `redirect_uri=` in `Details van verzoek` must now read `https://…`.

### `redirect_uri_mismatch` (the string doesn't match)

The `redirect_uri=` value in the error is the source of truth for what the app sent. Compare it
byte-for-byte with what is registered. Usual culprits: a trailing slash, `http` vs `https`,
`www.` vs bare host, a wrong host (proxy forwarding a different `Host`), or a stale registration.

### The login page shows no SSO button

The button appears only when the org's stored config is **enabled and complete** — discovery
URL, client id *and* client secret all set (Instellingen → Single sign-on). A half-configured
org never advertises a login that would refuse; the save itself rejects "enabled" with missing
fields, so check the org's settings page rather than the environment.

### "Single sign-on is not set up for this organization" on `/auth/oidc/login`

The routes are always mounted, but they answer per org: the org the *hostname* resolves to has
SSO disabled or incomplete. Multi-org installs: make sure you are on the right host — each org
brings its own IdP config.

### Locked out after enabling "Require single sign-on"

Set `SCHAKL_FORCE_LOCAL_LOGIN=true` on the API container and restart it — local password login
works again regardless of the stored setting. Sign in, fix or disable SSO in Instellingen →
Single sign-on, then remove the variable.

### Logged in via SSO but immediately 403 / "no access"

A JIT-provisioned SSO user needs a membership in the resolved org. With **auto-provision** on
(the default) the first login grants one at the configured default role. With it off, invite
the user first. If the host doesn't resolve to an org at all, no membership can be granted —
check the tenant's custom domain / slug.
