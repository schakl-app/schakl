import { redirect } from "@sveltejs/kit";

import type { RequestEvent } from "@sveltejs/kit";

import { AUTH_COOKIE_NAME } from "$lib/core/auth.server";
import { HANDOFF_RETURN_COOKIE, IMPERSONATION_COOKIE } from "$lib/core/impersonation";
import { apiFor } from "$lib/core/session";

/**
 * Ends an impersonation: audits the stop API-side, then drops the grant cookie here.
 *
 * A session that arrived through a cross-host handoff (#288) is dropped with it. That session
 * exists only so the grant has an administrator to name, and the administrator is generally not a
 * member of this org at all — leaving it behind would land them on a 403 dressed as a login
 * screen, on a hostname where they have no account. So it goes, and the operator lands on a page
 * that says the impersonation ended and points back to the console.
 *
 * That landing stays on *this* origin on purpose: our own `form-action 'self'` CSP blocks a form
 * submission whose redirect chain leaves the origin, and the banner's stop button is a form.
 */
export const POST = async (event: RequestEvent) => {
  await apiFor(event).POST("/api/v1/instance/impersonation/stop");
  event.cookies.delete(IMPERSONATION_COOKIE, { path: "/" });
  // No return cookie → this was a same-host impersonation (a box administering its own org), so
  // the operator's own session here is theirs to keep and home is where they belong.
  if (!event.cookies.get(HANDOFF_RETURN_COOKIE)) throw redirect(303, "/");
  event.cookies.delete(AUTH_COOKIE_NAME, { path: "/" });
  throw redirect(303, "/impersonate?stopped=1");
};
