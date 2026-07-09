import { redirect } from "@sveltejs/kit";

import type { RequestEvent } from "@sveltejs/kit";

import { IMPERSONATION_COOKIE } from "$lib/core/impersonation";

/**
 * Landing point for a cross-host impersonation grant (issue #26). It only stores the
 * grant cookie on *this* host and goes home — all validation happens API-side, where the
 * grant is only honoured alongside a live instance-owner session. Visiting this without
 * such a session changes nothing.
 */
export const GET = (event: RequestEvent) => {
  const token = event.url.searchParams.get("token");
  if (token) {
    event.cookies.set(IMPERSONATION_COOKIE, token, {
      path: "/",
      httpOnly: true,
      sameSite: "lax",
      secure: event.url.protocol === "https:",
      // The token carries its own expiry; the cookie just has to outlive it.
      maxAge: 60 * 60,
    });
  }
  throw redirect(303, "/");
};
