import { redirect } from "@sveltejs/kit";

import type { RequestEvent } from "@sveltejs/kit";

import { AUTH_COOKIE_NAME } from "$lib/core/auth.server";
import {
  HANDOFF_RETURN_COOKIE,
  IMPERSONATION_COOKIE,
  PORTAL_RETURN_COOKIE,
  safeReturnPath,
} from "$lib/core/impersonation";
import { apiFor } from "$lib/core/session";

/**
 * Ends an impersonation: audits the stop API-side, then drops the grant cookie here.
 *
 * Which endpoint audits it depends on the **kind**, and the kind comes from `/meta/me` via
 * `locals.user` — never from the submitted form. A hidden field would let the browser choose
 * which trail its own stop lands on, which is exactly the choice an audit trail exists to take
 * away from the person being audited.
 *
 * - `portal` (#296): agency staff signed in as a client's contact, on their own tenant. The stop
 *   lands on that contact's activity trail and the staff member returns to the contact they
 *   started from. Their own session here was never replaced, so it stays.
 * - `instance` (issue #26): an operator inside a tenant. A session that arrived through a
 *   cross-host handoff (#288) is dropped with the grant — it exists only so the grant has an
 *   administrator to name, and that administrator is generally not a member of this org at all,
 *   so leaving it behind would land them on a 403 dressed as a login screen.
 *
 * The landing stays on *this* origin either way: our own `form-action 'self'` CSP blocks a form
 * submission whose redirect chain leaves the origin, and the banner's stop button is a form.
 */
export const POST = async (event: RequestEvent) => {
  const api = apiFor(event);

  if (event.locals.user?.impersonationKind === "portal") {
    await api.POST("/api/v1/contacts/portal/impersonation/stop");
    event.cookies.delete(IMPERSONATION_COOKIE, { path: "/" });
    const back = safeReturnPath(event.cookies.get(PORTAL_RETURN_COOKIE));
    event.cookies.delete(PORTAL_RETURN_COOKIE, { path: "/" });
    throw redirect(303, back);
  }

  await api.POST("/api/v1/instance/impersonation/stop");
  event.cookies.delete(IMPERSONATION_COOKIE, { path: "/" });
  // No return cookie → this was a same-host impersonation (a box administering its own org), so
  // the operator's own session here is theirs to keep and home is where they belong.
  if (!event.cookies.get(HANDOFF_RETURN_COOKIE)) throw redirect(303, "/");
  event.cookies.delete(AUTH_COOKIE_NAME, { path: "/" });
  throw redirect(303, "/impersonate?stopped=1");
};
