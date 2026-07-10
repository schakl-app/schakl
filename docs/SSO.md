# Single sign-on (OIDC) — setup & the redirect URI

> How to point an external identity provider (Google, Entra ID, Authentik, Keycloak, …) at a
> vlotr install, and how to get the **redirect URI** right — the one thing that trips up every
> first setup. The environment variables themselves are tabulated in
> [`DEPLOY.md` → Single sign-on](DEPLOY.md#single-sign-on-oidc-off-by-default); this file is
> the operator's how-to and troubleshooting guide. Design boundaries for *Google Workspace*
> (login vs API access) live in [`GOOGLE.md`](GOOGLE.md).

## The redirect URI is fixed by the code

Every OIDC provider asks you to register one or more **authorized redirect URIs** (a.k.a.
callback URL, reply URL). For vlotr it is always, exactly:

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

The app **builds** this URL at request time from `request.url_for("oidc_callback")` — there is
no env var that overrides it. So the host and scheme the app *sees* are what get sent to the
provider. Getting those right is entirely about the reverse proxy (next section).

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

Also set `VLOTR_AUTH_COOKIE_SECURE=true` in production so the session cookie is only sent over
HTTPS. It is not what causes the mismatch, but it belongs to the same "we are behind TLS" story.

## Provider setup

### Any OIDC provider (Authentik, Keycloak, Entra ID, Auth0, …)

1. Register an application / OAuth client of type **web / confidential** (it has a client
   secret and does a server-side code exchange).
2. Set the **redirect URI** to `https://<your-host>/api/v1/auth/oidc/callback`.
3. Grant the scopes `openid email profile` (vlotr requests exactly these; `email` is required —
   the callback rejects a login with no email).
4. Note the **discovery URL** (`…/.well-known/openid-configuration`), the **client id**, and the
   **client secret**, and set the `VLOTR_OIDC_*` variables from
   [`DEPLOY.md`](DEPLOY.md#single-sign-on-oidc-off-by-default).

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
5. Configure vlotr:
   ```
   VLOTR_OIDC_ENABLED=true
   VLOTR_OIDC_DISCOVERY_URL=https://accounts.google.com/.well-known/openid-configuration
   VLOTR_OIDC_CLIENT_ID=<client id>.apps.googleusercontent.com
   VLOTR_OIDC_CLIENT_SECRET=<client secret>
   ```

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

`VLOTR_OIDC_ENABLED=true` alone is not enough — discovery URL, client id, **and** client secret
must all be set, or the routes and the button are withheld (and a startup `WARNING` names what
is missing). See [`DEPLOY.md`](DEPLOY.md#single-sign-on-oidc-off-by-default).

### The API refuses to start

`VLOTR_OIDC_ENFORCED=true` with OIDC not fully configured is a deliberate boot failure —
enforced OIDC turns off local login, so booting anyway would lock everyone out. Finish the OIDC
config or unset `VLOTR_OIDC_ENFORCED`.

### Logged in via SSO but immediately 403 / "no access"

A JIT-provisioned SSO user needs a membership in the resolved org. With
`VLOTR_OIDC_AUTO_PROVISION_MEMBERSHIP=true` (default) the first login grants one at
`VLOTR_OIDC_DEFAULT_ROLE`. With it `false`, invite the user first. If the host doesn't resolve
to an org at all, no membership can be granted — check the tenant's custom domain / slug.
