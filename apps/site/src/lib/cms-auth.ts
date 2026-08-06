// Sveltia CMS · GitHub sign-in — the shared half of the OAuth authorization-code flow.
//
// Two Pages Functions use this: functions/api/auth/index.ts starts the flow, and
// functions/api/auth/callback.ts finishes it. It lives here rather than under functions/
// because every file in that directory is a route, and Cloudflare does not document an
// exclusion for _-prefixed helpers — a shared module there is a gamble on undocumented
// behaviour for no gain.
//
// Why a server at all: GitHub still has no client-side PKCE for OAuth apps (announced for
// Q4 2025, unreleased as of writing), so the code→token exchange needs a client secret and
// therefore somewhere server-side to keep it. That is the whole job. See apps/site/AUTH.md.

export const PROVIDER = 'github';

/** The handshake the CMS and this popup exchange before the token is handed over. */
export const HANDSHAKE = `authorizing:${PROVIDER}`;

/**
 * Carries the CSRF state from /api/auth to /api/auth/callback. `Path` is the auth route and
 * not `/`, so it is never sent to the rest of the site; `SameSite=Lax` is required rather
 * than incidental — the cookie has to survive GitHub's top-level redirect back to us.
 */
const CSRF_COOKIE = 'cms-oauth-state';
const CSRF_MAX_AGE = 600; // 10 minutes: long enough to read a consent screen, short enough to matter.

export interface AuthEnv {
  GITHUB_CLIENT_ID: string;
  GITHUB_CLIENT_SECRET: string;
  /**
   * Scopes to request. The default is `public_repo,user`, which suits schakl-app/schakl being
   * public: it keeps the token away from the editor's private repositories entirely, where the
   * broader `repo` would have reached every one of them. The CMS behaves identically either way.
   * If this repo is ever made private, that stops being true — see AUTH.md.
   */
  GITHUB_SCOPE?: string;
  /** For GitHub Enterprise. The CMS's own `api_root` is configured separately in config.yml. */
  GITHUB_HOSTNAME?: string;
  /**
   * Origins allowed to receive the token, comma-separated. Defaults to this deployment's own
   * origin, which is the answer whenever the CMS is served from the same host as this function.
   */
  CMS_ALLOWED_ORIGINS?: string;
}

export const githubHost = (env: AuthEnv): string => env.GITHUB_HOSTNAME?.trim() || 'github.com';

/**
 * The callback is derived from the incoming request rather than configured, so the value sent
 * to GitHub and the route that receives it can never drift apart. A preview deployment on a
 * different hostname therefore fails with GitHub's own "redirect_uri mismatch" — which is the
 * honest error. Omitting the parameter instead would send the popup to the *production*
 * callback, where the state cookie does not exist, and report a CSRF attack that never happened.
 */
export const redirectURI = (request: Request): string =>
  new URL('/api/auth/callback', request.url).toString();

export const allowedOrigins = (request: Request, env: AuthEnv): string[] => {
  const configured = (env.CMS_ALLOWED_ORIGINS ?? '')
    .split(',')
    .map((s) => s.trim().replace(/\/$/, ''))
    .filter(Boolean);

  return configured.length ? configured : [new URL(request.url).origin];
};

export const issueStateCookie = (state: string): string =>
  `${CSRF_COOKIE}=${state}; HttpOnly; Path=/api/auth; Max-Age=${CSRF_MAX_AGE}; SameSite=Lax; Secure`;

const CLEAR_STATE_COOKIE = `${CSRF_COOKIE}=; HttpOnly; Path=/api/auth; Max-Age=0; SameSite=Lax; Secure`;

export const readStateCookie = (request: Request): string => {
  const header = request.headers.get('Cookie') ?? '';
  const match = header.match(new RegExp(`(?:^|;\\s*)${CSRF_COOKIE}=([0-9a-f]{32})(?:;|$)`));

  return match?.[1] ?? '';
};

/** Constant-time compare, so the state check leaks nothing through timing. */
export const sameState = (a: string, b: string): boolean => {
  if (!a || !b || a.length !== b.length) return false;

  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);

  return diff === 0;
};

/**
 * Embed a value as a JS literal. `<` is escaped because a `</script>` inside an error string
 * would otherwise end the block and turn a failed login into stored markup.
 */
const jsLiteral = (value: unknown): string =>
  JSON.stringify(value)
    .replace(/</g, '\\u003c')
    .replace(/[\u2028\u2029]/g, (c) => (c === '\u2028' ? '\\u2028' : '\\u2029'));

interface OutputArgs {
  request: Request;
  env: AuthEnv;
  token?: string;
  error?: string;
  /** Sveltia localizes the message from this code, which is why the prose above stays English. */
  errorCode?: string;
}

/**
 * The page GitHub lands on. It hands the result to the CMS window through postMessage and
 * nothing else — there is no session and no cookie beyond clearing the state.
 *
 * It deliberately does NOT reply to whichever origin sent the handshake, which is what the
 * upstream sveltia-cms-auth worker does. That worker echoes `event.origin` back, so any page
 * that opens this popup and posts `authorizing:github` receives the token — and for a user who
 * has already approved the OAuth app, GitHub redirects without showing a consent screen, so the
 * whole theft is silent. Its ALLOWED_DOMAINS check does not close this: it validates `site_id`,
 * a query parameter the attacker sets. Pinning the target origin to a list we control does.
 */
export const outputHTML = ({ request, env, token, error, errorCode }: OutputArgs): Response => {
  const state = error ? 'error' : 'success';
  const content = error ? { provider: PROVIDER, error, errorCode } : { provider: PROVIDER, token };
  const message = `authorization:${PROVIDER}:${state}:${JSON.stringify(content)}`;

  const body = `<!doctype html>
<html lang="nl">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex" />
    <title>CMS</title>
  </head>
  <body style="font: 16px/1.5 system-ui, sans-serif; margin: 3rem auto; max-width: 28rem; padding: 0 1rem">
    <p>Je kunt dit venster sluiten.</p>
    <p lang="en">You can close this window.</p>
    <script>
      (() => {
        const ALLOWED = ${jsLiteral(allowedOrigins(request, env))};
        const MESSAGE = ${jsLiteral(message)};
        const HANDSHAKE = ${jsLiteral(HANDSHAKE)};
        const opener = window.opener;
        if (!opener) return;

        window.addEventListener('message', (event) => {
          if (event.data !== HANDSHAKE || !ALLOWED.includes(event.origin)) return;
          opener.postMessage(MESSAGE, event.origin);
        });

        // Announce to each permitted origin rather than '*'. The browser drops the ones that
        // do not match the opener, so exactly the right window is reached and no other.
        for (const origin of ALLOWED) opener.postMessage(HANDSHAKE, origin);
      })();
    </script>
  </body>
</html>`;

  return new Response(body, {
    headers: {
      'Content-Type': 'text/html;charset=UTF-8',
      'Cache-Control': 'no-store',
      'Set-Cookie': CLEAR_STATE_COOKIE,
    },
  });
};
