import { redirect } from "@sveltejs/kit";

import { AUTH_COOKIE_NAME } from "$lib/core/auth.server";
import { apiErrorKey } from "$lib/core/errors";
import { HANDOFF_RETURN_COOKIE, IMPERSONATION_COOKIE } from "$lib/core/impersonation";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad, RequestEvent } from "./$types";

/**
 * Landing point for a cross-host impersonation crossing (issue #26, fixed in #288).
 *
 * Cookies are host-scoped, so the instance console — the apex of a cloud install, or another
 * org's host on a multi-org box — cannot put anything on this hostname. It hands the browser a
 * single-use **ticket** instead, and this route redeems it server-side for the two cookies the
 * API expects to see together: a session for the *real* administrator, and the grant naming them.
 * Nothing in the URL is a credential on its own, and nothing here trusts the query string beyond
 * passing it back to the API, which re-checks the ticket against this host, the org, the
 * administrator's capability and the target member.
 *
 * A failed or expired ticket **renders** (see `+page.svelte`) rather than redirecting: bouncing to
 * `/login` on a hostname the operator has no account on was the whole confusing symptom of #288.
 */
export const load: PageServerLoad = async (event) => {
  // Where a stopped impersonation lands (see `/impersonation/stop`): nothing left to do but say
  // so and hand back the way to the console, which is also where the return cookie is spent.
  if (event.url.searchParams.has("stopped")) {
    const consoleUrl = event.cookies.get(HANDOFF_RETURN_COOKIE) ?? null;
    event.cookies.delete(HANDOFF_RETURN_COOKIE, { path: "/" });
    return { stopped: true, error: null, consoleUrl };
  }

  const ticket = event.url.searchParams.get("ticket") ?? "";
  if (!ticket) {
    return { stopped: false, error: "errors.impersonation_handoff_invalid", consoleUrl: null };
  }

  const { data, error } = await apiFor(event).POST("/api/v1/instance/impersonation/claim", {
    body: { ticket },
  });
  if (error || !data) {
    return {
      stopped: false,
      error: apiErrorKey(error, "errors.impersonation_handoff_invalid").key,
      // The claim failed, so ask the instance itself where its console lives.
      consoleUrl: await consoleUrlFromMeta(event),
    };
  }

  // Both cookies live exactly as long as the grant does: an operator's session on a customer's
  // hostname should not outlast the reason it was created.
  const options = {
    path: "/",
    httpOnly: true,
    sameSite: "lax",
    secure: event.url.protocol === "https:",
    maxAge: data.max_age,
  } as const;
  event.cookies.set(AUTH_COOKIE_NAME, data.session_token, options);
  event.cookies.set(IMPERSONATION_COOKIE, data.token, options);
  // Remember where to send the operator when they stop — the console's own host, named by the
  // API from its own configuration and never by a parameter, so it cannot be steered. The scheme
  // and port are ours to add: only this side knows what the browser is actually speaking.
  if (data.console_host) {
    event.cookies.set(HANDOFF_RETURN_COOKIE, originOn(event, data.console_host), options);
  }
  throw redirect(303, "/");
};

function originOn(event: RequestEvent, host: string): string {
  const port = event.url.port ? `:${event.url.port}` : "";
  return `${event.url.protocol}//${host}${port}`;
}

/** The console's origin, for the "back to the console" link on the failure screen. */
async function consoleUrlFromMeta(event: RequestEvent): Promise<string | null> {
  const { data } = await apiFor(event).GET("/api/v1/meta/instance");
  if (!data || data.deployment !== "cloud" || !data.base_domain) return null;
  return originOn(event, data.base_domain);
}
