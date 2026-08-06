# CMS sign-in (GitHub OAuth) — how it works and how to turn it on

> Internal note for contributors. The CMS at `/admin` can already edit content **locally**;
> this describes the small service that adds hosted "Sign in with GitHub", and the one-time
> account wiring that activates it. Nothing here is user-facing product documentation.

## Why a server is involved at all

Sveltia CMS talks to GitHub from the browser, so it needs a GitHub access token. Getting one
means the OAuth **authorization code flow**, whose second leg — trading the `code` for a token —
requires the OAuth app's **client secret**. A secret cannot live in a static bundle, so it needs
somewhere server-side. That is the entire job of this service: two routes, no database, no session.

The escape hatch would be PKCE, which removes the secret. GitHub has **not** shipped client-side
PKCE for OAuth apps (announced for Q4 2025, still unreleased), so it is not an option today —
Sveltia's own docs carry a warning that AI tools tend to claim otherwise. If GitHub ever ships it,
this whole directory is deleted and `config.yml` gains `auth_type: pkce`.

The default Sveltia behaviour, had we configured nothing, is to send editors through
`https://api.netlify.com/auth` — Netlify's OAuth client, for sites hosted on Netlify. This isn't.

## The pieces

| Where | What |
|---|---|
| `functions/api/auth/index.ts` | `GET /api/auth` — mints a CSRF state, sets it as a cookie, 302s to GitHub's consent screen. |
| `functions/api/auth/callback.ts` | `GET /api/auth/callback` — checks the state, exchanges the code for a token, returns the popup page. |
| `src/lib/cms-auth.ts` | Shared: cookie shape, allowed origins, and the HTML page that hands the token over. |
| `public/sveltia/config.yml` | `base_url` + `auth_endpoint`, which is how the CMS finds `/api/auth`. |

It lives in `src/lib/` rather than `functions/_shared/` because every file under `functions/` is a
route and Cloudflare documents no exclusion for `_`-prefixed helpers.

### The flow

1. An editor clicks "Sign in with GitHub" at `/admin`. The CMS opens a popup at
   `<base_url>/<auth_endpoint>` — `https://schakl.app/api/auth?provider=github&site_id=schakl.app`.
2. `/api/auth` generates a random state, sets it in an `HttpOnly; Secure; SameSite=Lax` cookie
   scoped to `Path=/api/auth`, and redirects to GitHub. `SameSite=Lax` is load-bearing, not
   incidental: the cookie has to survive GitHub's top-level redirect back to us.
3. GitHub sends the editor to `/api/auth/callback?code=…&state=…`.
4. The callback compares the state against the cookie (constant time), POSTs the code plus the
   client secret to GitHub, and gets an access token.
5. The popup posts the token to the CMS window via `postMessage`, and the cookie is cleared.

## One-time wiring

Steps 1 and 2 need accounts, so they can't be scripted from the repo.

**1. Register the GitHub OAuth app** — <https://github.com/settings/developers> → *New OAuth App*
(for an org-owned app, do it under the `schakl-app` org settings instead, so access survives one
person leaving).

| Field | Value |
|---|---|
| Application name | `schakl CMS` |
| Homepage URL | `https://schakl.app` |
| Authorization callback URL | `https://schakl.app/api/auth/callback` |

Generate a client secret and keep it to hand; GitHub shows it once.

**2. Deploy the site on Cloudflare Pages** with the repo connected:

| Setting | Value |
|---|---|
| Root directory | `apps/site` |
| Build command | `pnpm build` |
| Output directory | `dist` |

Pages picks up `apps/site/functions/` automatically — that is also what finally activates the
contact/interest forms, which have been waiting on the same deploy (see `FORMS.md`).

**3. Set the secrets** — Pages project → *Settings* → *Environment variables*, encrypted:

| Variable | Value |
|---|---|
| `GITHUB_CLIENT_ID` | from the OAuth app |
| `GITHUB_CLIENT_SECRET` | from the OAuth app (encrypt this one) |

Optional:

| Variable | Default | Why you'd set it |
|---|---|---|
| `GITHUB_SCOPE` | `public_repo,user` | Set to `repo,user` if `schakl-app/schakl` is ever made private — see below. |
| `CMS_ALLOWED_ORIGINS` | this deployment's own origin | Only needed if the CMS is served from a different host than this function. |
| `GITHUB_HOSTNAME` | `github.com` | GitHub Enterprise. |

**4. Check it.** Open `https://schakl.app/admin`, click "Sign in with GitHub", approve. A save
should land as a commit on `dev`.

## Access control

There isn't any here, and that is deliberate — the OAuth app hands back a token for whoever signs
in, and **the repository's own permissions decide what happens next**. Someone with no write access
to `schakl-app/schakl` can sign in and will fail at the first save. So: manage CMS editors by
managing repo collaborators. Nothing in this service needs to change when the editor list does.

### Scope

`schakl-app/schakl` is public, so the request is for **`public_repo,user`** rather than the `repo`
that upstream and most guides use. `repo` is the all-repositories grant: it would hand this service
a token that can read and write every private repository the editor can reach, for a CMS that edits
one public one. `public_repo` cannot touch a private repo at all.

It is still not per-repository — it covers all of the editor's *public* repos — because that is
GitHub's granularity for OAuth apps, not a choice made here. A GitHub App with per-repo installation
is the finer-grained upgrade path if that ever matters.

**If this repo is ever made private, sign-in breaks**, and confusingly: the token is issued fine and
the CMS then 404s on the repository, because a `public_repo` token cannot see it. The fix is setting
`GITHUB_SCOPE=repo,user` — no code change, no redeploy. Worth remembering, because "the CMS says the
repo doesn't exist" does not obviously point at a scope.

## A deliberate difference from upstream `sveltia-cms-auth`

The obvious move is deploying the upstream worker as-is. This is a reimplementation because of one
line in it. Upstream replies to whichever origin sent the handshake:

```js
window.addEventListener('message', ({ data, origin }) => {
  if (data === 'authorizing:github') {
    window.opener?.postMessage('authorization:github:success:…', origin); // ← origin is the sender's
  }
});
```

Any page can open that popup and post `authorizing:github` to receive the token. For an editor who
has already approved the OAuth app, GitHub redirects **without showing a consent screen**, so the
theft is silent and needs one click on an unrelated site. Upstream's `ALLOWED_DOMAINS` does not
close it: that check validates `site_id`, a query parameter the attacker supplies.

Here the target origin is pinned to a list this service controls (`CMS_ALLOWED_ORIGINS`, defaulting
to its own origin), the handshake is announced to those origins rather than `*`, and a message from
anywhere else is ignored. Everything else — the flow, the message strings, the error codes Sveltia
localizes — matches upstream, because the CMS depends on that protocol exactly.

## Local development

Untouched by all this. `pnpm site cms` → `/admin` → **"Work with Local Repository"** (Chrome/Edge),
pick the repo folder, and saves write straight into the working tree with no token anywhere. That
remains the fastest way to edit content, and the only way that works offline.

To exercise the hosted flow locally you need the functions runtime and a second OAuth app whose
callback is `http://localhost:8788/api/auth/callback`:

```bash
pnpm site build
npx wrangler pages dev apps/site/dist   # from the repo root; serves functions/ too
```

Then temporarily point `base_url` at `http://localhost:8788`. Don't commit that.
