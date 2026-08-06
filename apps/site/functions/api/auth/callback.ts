// Cloudflare Pages Function — GET /api/auth/callback
//
// Step two of the Sveltia CMS GitHub sign-in, and the only reason this service exists: trade
// the authorization code for a token using the client secret, then hand the token to the CMS
// window. This URL is what goes in the OAuth app's "Authorization callback URL" field.
//
// Nothing is persisted. The token goes to the browser and the state cookie is cleared.

import {
  type AuthEnv,
  githubHost,
  outputHTML,
  readStateCookie,
  redirectURI,
  sameState,
} from '../../../src/lib/cms-auth';

export const onRequestGet: PagesFunction<AuthEnv> = async ({ request, env }) => {
  const { searchParams } = new URL(request.url);
  const code = searchParams.get('code') ?? '';
  const state = searchParams.get('state') ?? '';

  // GitHub reports a refused consent here rather than by omitting the code. Reading it first
  // means "I clicked Cancel" does not surface as a CSRF warning.
  const denied = searchParams.get('error');
  if (denied) {
    return outputHTML({
      request,
      env,
      error: searchParams.get('error_description') || denied,
      errorCode: 'AUTH_CODE_REQUEST_FAILED',
    });
  }

  if (!code || !state) {
    return outputHTML({
      request,
      env,
      error: 'Failed to receive an authorization code. Please try again later.',
      errorCode: 'AUTH_CODE_REQUEST_FAILED',
    });
  }

  if (!sameState(readStateCookie(request), state)) {
    return outputHTML({
      request,
      env,
      error: 'Potential CSRF attack detected. Authentication flow aborted.',
      errorCode: 'CSRF_DETECTED',
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

  let response: Response;

  try {
    response = await fetch(`https://${githubHost(env)}/login/oauth/access_token`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code,
        client_id: env.GITHUB_CLIENT_ID,
        client_secret: env.GITHUB_CLIENT_SECRET,
        // Sent because /api/auth sent it: GitHub requires the two to agree when it is present.
        redirect_uri: redirectURI(request),
      }),
    });
  } catch {
    return outputHTML({
      request,
      env,
      error: 'Failed to request an access token. Please try again later.',
      errorCode: 'TOKEN_REQUEST_FAILED',
    });
  }

  // GitHub answers 200 with an `error` field rather than a status code, so the body is the
  // authority on success here, not `response.ok`.
  let payload: { access_token?: string; error?: string; error_description?: string };

  try {
    payload = await response.json();
  } catch {
    return outputHTML({
      request,
      env,
      error: 'Server responded with malformed data. Please try again later.',
      errorCode: 'MALFORMED_RESPONSE',
    });
  }

  const token = payload.access_token ?? '';

  if (!token) {
    return outputHTML({
      request,
      env,
      error:
        payload.error_description ||
        payload.error ||
        'No access token was returned. Please try again later.',
      errorCode: 'TOKEN_REQUEST_FAILED',
    });
  }

  return outputHTML({ request, env, token });
};
