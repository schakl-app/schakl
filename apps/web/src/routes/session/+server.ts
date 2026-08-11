import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

/**
 * "Am I still signed in?" — the one question a tab cannot answer for itself.
 *
 * The session cookie is httpOnly, so the browser cannot look; and a page that has already been
 * rendered goes on looking signed in for as long as nobody navigates. This is what the shell
 * asks when a tab comes back to the foreground, and it costs **nothing beyond the round-trip**:
 * `hooks.server.ts` resolves `locals.user` on every request already, so there is no API call
 * here that the request was not making anyway.
 *
 * `?options=1` adds what the re-login dialog needs to draw itself (is there a password form on
 * this org at all, or only SSO). Asked for only when the dialog actually opens, so the ordinary
 * probe stays one question and one answer.
 *
 * `no-store` is not optional: a PWA sits in front of this, and a cached "yes" is a wall that
 * never comes down.
 */
export const GET: RequestHandler = async (event) => {
  const signedIn = !!event.locals.user;
  // *Who*, not just whether: the dialog re-reads the page only when the person changed, because
  // a re-read is the one thing in the recovery that can overwrite what somebody had typed.
  const body: Record<string, unknown> = { signedIn, userId: event.locals.user?.id ?? null };

  if (!signedIn && event.url.searchParams.has("options")) {
    // Per-org and resolved from the hostname, exactly as the login screen resolves it (#76) —
    // never a second opinion about which sign-in methods this tenant offers.
    const { data } = await apiFor(event).GET("/api/v1/meta/modules");
    body.localLogin = data?.local_login_enabled ?? true;
    body.oidcEnabled = data?.oidc_enabled ?? false;
    body.oidcName = data?.oidc_name ?? null;
  }

  return json(body, { headers: { "cache-control": "no-store" } });
};
