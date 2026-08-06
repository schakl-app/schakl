// Cloudflare Pages Function — GET /api/auth
//
// Step one of the Sveltia CMS GitHub sign-in: mint a CSRF state, park it in a cookie, and
// bounce the popup to GitHub's consent screen. Step two is ./callback.ts. The shared pieces
// (cookie shape, allowed origins, the postMessage page) live in src/lib/cms-auth.ts, and
// apps/site/AUTH.md holds the one-time wiring.
//
// The CMS calls this because config.yml sets `base_url` + `auth_endpoint`; it appends
// ?provider=github&site_id=<hostname> of its own accord.

import {
  type AuthEnv,
  PROVIDER,
  githubHost,
  issueStateCookie,
  outputHTML,
  redirectURI,
} from '../../../src/lib/cms-auth';

export const onRequestGet: PagesFunction<AuthEnv> = async ({ request, env }) => {
  const provider = new URL(request.url).searchParams.get('provider') ?? PROVIDER;

  // This deployment speaks GitHub only. Saying so through the popup rather than as a bare
  // status code is what lets the CMS render the failure instead of hanging on a dead window.
  if (provider !== PROVIDER) {
    return outputHTML({
      request,
      env,
      error: 'Only the GitHub backend is supported by this authenticator.',
      errorCode: 'UNSUPPORTED_BACKEND',
    });
  }

  if (!env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET) {
    return outputHTML({
      request,
      env,
      error: 'OAuth app client ID or secret is not configured.',
      errorCode: 'MISCONFIGURED_CLIENT',
    });
  }

  const state = crypto.randomUUID().replaceAll('-', '');
  const params = new URLSearchParams({
    client_id: env.GITHUB_CLIENT_ID,
    redirect_uri: redirectURI(request),
    scope: env.GITHUB_SCOPE?.trim() || 'public_repo,user',
    state,
  });

  return new Response('', {
    status: 302,
    headers: {
      Location: `https://${githubHost(env)}/login/oauth/authorize?${params}`,
      'Cache-Control': 'no-store',
      'Set-Cookie': issueStateCookie(state),
    },
  });
};
